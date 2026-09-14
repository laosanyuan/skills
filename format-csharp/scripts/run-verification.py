#!/usr/bin/env python3
"""Run an already-authorized argv with bounded lifetime and separate log files."""

import argparse
import ctypes
import json
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time
import uuid


class ArgumentError(ValueError):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ArgumentError(message)


def checked_directory(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ArgumentError("directories must be absolute and contain no '..': " + value)
    for component in [*reversed(path.parents), path]:
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ArgumentError("symlink/reparse directory is not allowed: " + str(component))
        if not stat.S_ISDIR(info.st_mode):
            raise ArgumentError("not a directory: " + str(component))
    return path.resolve(strict=True)


def arguments(argv):
    parser = Parser(description=__doc__)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--artifacts-root", required=True)
    parser.add_argument("--timeout", type=float, required=True, help="positive finite seconds")
    parser.add_argument("--profile", choices=("plain", "dotnet"), required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ArgumentError("--timeout must be positive and finite")
    if not args.command or args.command[0] != "--" or len(args.command) < 2:
        raise ArgumentError("provide a nonempty argv after '--'")
    args.command = args.command[1:]
    if not args.command[0] or any("\0" in part for part in args.command):
        raise ArgumentError("executable must be nonempty and argv cannot contain NUL")
    if os.name == "nt" and args.command[0].rstrip(" .").lower().endswith((".cmd", ".bat")):
        raise ArgumentError("direct Windows .cmd/.bat execution is not supported; use an actual "
                            "executable or an explicitly reviewed and authorized interpreter")
    args.cwd = checked_directory(args.cwd)
    args.artifacts_root = checked_directory(args.artifacts_root)
    if args.artifacts_root == Path(args.artifacts_root.anchor):
        raise ArgumentError("--artifacts-root cannot be a filesystem root")
    return args


def command_environment(profile, run_dir):
    environment = os.environ.copy()
    if profile == "dotnet":
        environment.update({
            "DOTNET_GENERATE_ASPNET_CERTIFICATE": "false",
            "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
            "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
            "DOTNET_ADD_GLOBAL_TOOLS_TO_PATH": "false",
        })
        for key, name in (("DOTNET_CLI_HOME", "dotnet-home"),
                          ("NUGET_PACKAGES", "nuget-packages"),
                          ("TEMP", "temp"), ("TMP", "temp")):
            target = run_dir / name
            target.mkdir(exist_ok=True)
            environment[key] = str(target)
    return environment


class WindowsJob:
    """Assign the suspended child before it can spawn descendants."""

    def __init__(self):
        from ctypes import wintypes as w

        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", w.DWORD), ("min_ws", ctypes.c_size_t),
                        ("max_ws", ctypes.c_size_t), ("active_limit", w.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", w.DWORD),
                        ("scheduling", w.DWORD)]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", ctypes.c_ulonglong * 6),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

        class ThreadEntry(ctypes.Structure):
            _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("thread_id", w.DWORD),
                        ("process_id", w.DWORD), ("base_priority", w.LONG),
                        ("delta_priority", w.LONG), ("flags", w.DWORD)]

        class Accounting(ctypes.Structure):
            _fields_ = [("user_time", ctypes.c_longlong), ("kernel_time", ctypes.c_longlong),
                        ("period_user", ctypes.c_longlong), ("period_kernel", ctypes.c_longlong),
                        ("page_faults", w.DWORD), ("total_processes", w.DWORD),
                        ("active_processes", w.DWORD), ("terminated_processes", w.DWORD)]

        self.entry_type = ThreadEntry
        self.accounting_type = Accounting
        self.assigned = False
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "QueryInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                           w.DWORD, ctypes.c_void_p], w.BOOL),
            "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "CreateToolhelp32Snapshot": ([w.DWORD, w.DWORD], w.HANDLE),
            "Thread32First": ([w.HANDLE, ctypes.POINTER(ThreadEntry)], w.BOOL),
            "Thread32Next": ([w.HANDLE, ctypes.POINTER(ThreadEntry)], w.BOOL),
            "OpenThread": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "ResumeThread": ([w.HANDLE], w.DWORD),
            "CloseHandle": ([w.HANDLE], w.BOOL),
        }
        for name, (argtypes, restype) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = argtypes, restype
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def start(self, process):
        handle = self.api.OpenProcess(0x0100 | 0x0001, False, process.pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.api.AssignProcessToJobObject(self.handle, handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.assigned = True
        finally:
            self.close_handle(handle)
        # Popen closes the primary thread handle; the still-suspended process has
        # exactly its initial thread. Reopen only that process's thread to resume it.
        snapshot = self.api.CreateToolhelp32Snapshot(0x00000004, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = self.entry_type()
            entry.size = ctypes.sizeof(entry)
            found = self.api.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.process_id == process.pid:
                    thread = self.api.OpenThread(0x0002, False, entry.thread_id)
                    if not thread:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        if self.api.ResumeThread(thread) == 0xFFFFFFFF:
                            raise ctypes.WinError(ctypes.get_last_error())
                        return
                    finally:
                        self.close_handle(thread)
                found = self.api.Thread32Next(snapshot, ctypes.byref(entry))
            raise OSError("suspended child thread was not found")
        finally:
            self.close_handle(snapshot)

    def terminate(self):
        if not self.api.TerminateJobObject(self.handle, 124):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.close_handle(self.handle)
            self.handle = None


    def close_handle(self, handle):
        if not self.api.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def active_processes(self):
        accounting = self.accounting_type()
        if not self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting),
                                                 ctypes.sizeof(accounting), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return accounting.active_processes


def cleanup_process(process, job, deadline):
    """One cumulative cleanup deadline; signal acceptance is not exit evidence."""
    cleanup = {"method": "none", "requested": False, "request": None, "request_result": "not_requested",
               "result": "not_needed", "parent_exited": process is None,
               "close_result": "not_applicable", "errors": []}
    try:
        if process is not None:
            cleanup["method"] = ("windows_job" if job and job.assigned else
                                 "suspended_process" if job else "posix_process_group")
            cleanup["requested"] = True
            cleanup["request"] = ("TerminateJobObject" if job and job.assigned else
                                  "kill_suspended_process" if job else "killpg(SIGKILL)")
            cleanup["result"] = "unconfirmed"
            try:
                if job and job.assigned:
                    job.terminate()
                elif job:
                    # Assignment failed before the initial thread could run.
                    process.kill()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                cleanup["request_result"] = "accepted"
            except OSError as error:
                cleanup["request_result"] = "failed"
                cleanup["errors"].append("termination request: " + str(error))
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
                cleanup["parent_exited"] = True
            except subprocess.TimeoutExpired:
                cleanup["errors"].append("parent exit was not observed before cleanup deadline")
            while cleanup["parent_exited"]:
                if job and job.assigned:
                    cleanup["active_processes"] = job.active_processes()
                    exited = cleanup["active_processes"] == 0
                elif job:
                    exited = True  # An unassigned child was never resumed.
                else:
                    try:
                        os.killpg(process.pid, 0)
                        exited = False
                    except ProcessLookupError:
                        exited = True
                if exited:
                    cleanup["result"] = "confirmed_exited"
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(0.01, remaining))
    except (OSError, subprocess.SubprocessError) as error:
        cleanup["errors"].append("exit observation: " + str(error))
    finally:
        if job:
            cleanup["close_action"] = "close_job_with_kill_on_close"
            try:
                job.close()
                cleanup["close_result"] = "succeeded"
            except OSError as error:
                cleanup["close_result"] = "failed"
                cleanup["errors"].append("CloseHandle(job): " + str(error))
            # Closing a Job can request termination, but does not prove exit.
    return cleanup


def wait_for_exit(process, started, timeout):
    # Bound each OS wait even when the requested finite timeout exceeds DWORD milliseconds.
    while True:
        remaining = max(0, timeout - (time.monotonic() - started))
        try:
            return process.wait(timeout=min(60, remaining))
        except subprocess.TimeoutExpired:
            if time.monotonic() - started >= timeout:
                raise


def execute(args, report):
    job = None
    process = None
    started = None
    observed = None
    launch_completed = False
    try:
        with open(report["stdout_log"], "xb") as stdout, open(report["stderr_log"], "xb") as stderr:
            environment = command_environment(args.profile, Path(report["run_dir"]))
            options = {"start_new_session": True}
            if os.name == "nt":
                job = WindowsJob()
                options = {"creationflags": subprocess.CREATE_NO_WINDOW | 0x00000004}
            started = time.monotonic()
            process = subprocess.Popen(args.command, cwd=args.cwd, env=environment,
                                       stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                       shell=False, close_fds=True, **options)
            report["pid"] = process.pid
            if job:
                job.start(process)
            launch_completed = True
            try:
                wait_for_exit(process, started, args.timeout)
                observed = time.monotonic()
                report["status"] = "success" if process.returncode == 0 else "failed"
            except subprocess.TimeoutExpired:
                observed = time.monotonic()
                report["status"] = "timeout"
                report["timed_out"] = True
            report["exit_code"] = process.returncode
    except KeyboardInterrupt:
        observed = observed or time.monotonic()
        report["status"] = "interrupted"
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        observed = observed or time.monotonic()
        report["status"] = "launch_error" if not launch_completed else "runner_error"
        report["error"] = str(error)
    finally:
        report["command_status"] = report["status"]
        interval = (observed - started) if started is not None and observed is not None else 0.0
        report["command_interval_elapsed"] = round(interval, 6)
        cleanup_started = time.monotonic()
        cleanup = cleanup_process(process, job, cleanup_started + 5)
        report["cleanup"] = cleanup
        cleanup_elapsed = time.monotonic() - cleanup_started
        report["cleanup_elapsed"] = round(cleanup_elapsed, 6)
        report["command_elapsed"] = round(interval + cleanup_elapsed, 6)
        if cleanup["errors"] or cleanup["result"] == "unconfirmed":
            report["status"] = "runner_error"
        if process is not None:
            report["exit_code"] = process.returncode


def main(argv=None):
    started = time.monotonic()
    report = {"status": "invalid_arguments", "command_status": None, "exit_code": None,
              "timed_out": False, "command_elapsed": 0.0,
              "command_interval_elapsed": 0.0, "cleanup_elapsed": 0.0,
              "cleanup": {"method": "none", "requested": False, "request": None,
                          "request_result": "not_requested",
                          "result": "not_needed", "parent_exited": True,
                          "close_result": "not_applicable", "errors": []},
              "total_elapsed": 0.0, "run_dir": None, "stdout_log": None, "stderr_log": None}
    try:
        args = arguments(sys.argv[1:] if argv is None else argv)
        # mkdir is exclusive; names are never reused and old artifacts are never cleaned.
        run_dir = args.artifacts_root / ("verification-" + uuid.uuid4().hex)
        run_dir.mkdir()
        run_dir = checked_directory(str(run_dir))
        if run_dir.parent != args.artifacts_root:
            raise ArgumentError("run directory escaped --artifacts-root")
        report.update(command=args.command, cwd=str(args.cwd), profile=args.profile,
                      timeout=args.timeout, run_dir=str(run_dir),
                      stdout_log=str(run_dir / "stdout.log"), stderr_log=str(run_dir / "stderr.log"))
        execute(args, report)
    except (ArgumentError, OSError, ValueError) as error:
        report["error"] = str(error)
    report["total_elapsed"] = round(time.monotonic() - started, 6)
    if report["run_dir"]:
        try:
            with open(Path(report["run_dir"]) / "result.json", "x", encoding="utf-8") as output:
                json.dump(report, output, ensure_ascii=False, indent=2)
                output.write("\n")
        except OSError as error:
            report["status"] = "runner_error"
            report["error"] = "cannot write result.json: " + str(error)
    print(json.dumps(report, ensure_ascii=True))
    return {"success": 0, "failed": 1, "timeout": 124, "launch_error": 126,
            "invalid_arguments": 2, "interrupted": 130}.get(report["status"], 125)


if __name__ == "__main__":
    sys.exit(main())
