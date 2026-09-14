#!/usr/bin/env python3
"""Optional read-only Roslyn comment audit; requires an installed stable .NET SDK."""

import argparse
import importlib.util
import json
import math
from pathlib import Path
import re
import shutil
import sys
from types import SimpleNamespace
import uuid

sys.dont_write_bytecode = True
from scan_scope import read_source, resolve_cs_files


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def run_command(runner, run_dir, command, timeout):
    directory = run_dir / ("command-" + uuid.uuid4().hex)
    directory.mkdir()
    result = {"status": "launch_error", "timed_out": False,
              "run_dir": str(directory), "stdout_log": str(directory / "stdout.log"),
              "stderr_log": str(directory / "stderr.log"), "command": command}
    runner.execute(SimpleNamespace(profile="dotnet", command=command, cwd=run_dir,
                                   timeout=timeout), result)
    (directory / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    if result["status"] != "success":
        raise ValueError("Command failed; inspect " + str(directory / "result.json"))
    return Path(result["stdout_log"]).read_text(encoding="utf-8-sig")


def main(argv=None):
    report = {"schema_version": 1, "status": "error", "run_dir": None,
              "note": "A completed scan is not proof of overall compliance"}
    run_dir = None
    exit_code = 2
    try:
        parser = Parser(description=__doc__)
        parser.add_argument("--scope", required=True)
        parser.add_argument("--include-from")
        parser.add_argument("--include-generated", action="store_true")
        parser.add_argument("--encoding", default="auto")
        parser.add_argument("--artifacts-root", required=True)
        parser.add_argument("--sdk-version", help="Exact installed stable SDK; default: highest stable")
        parser.add_argument("--language-version", default="default")
        parser.add_argument("--define", action="append", default=[], help="One preprocessor symbol per option")
        parser.add_argument("--timeout", type=float, default=120, help="Seconds per subprocess, plus cleanup")
        args = parser.parse_args(argv)
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError("--timeout must be positive and finite")
        support = Path(__file__).resolve().parent
        spec = importlib.util.spec_from_file_location("verification_runner", support / "run-verification.py")
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        artifacts = runner.checked_directory(args.artifacts_root)
        if artifacts == Path(artifacts.anchor):
            raise ValueError("--artifacts-root cannot be a filesystem root")
        scope = Path(args.scope).resolve(strict=True)
        if scope.is_dir() and artifacts.is_relative_to(scope):
            raise ValueError("--artifacts-root must be outside directory --scope to prevent scan self-contamination")
        files = resolve_cs_files(scope, Path(args.include_from) if args.include_from else None,
                                 args.include_generated, args.encoding)
        if not files:
            raise ValueError("No eligible .cs files in scope")
        sources = [{"path": str(path), "text": read_source(path, args.encoding)} for path in files]
        dotnet = shutil.which("dotnet")
        if not dotnet:
            raise ValueError("No installed dotnet executable; use the separate regex scanner explicitly")
        run_dir = artifacts / ("comment-syntax-" + uuid.uuid4().hex)
        run_dir.mkdir()
        run_dir = runner.checked_directory(str(run_dir))
        report["run_dir"] = str(run_dir)
        sdk_text = run_command(runner, run_dir, [dotnet, "--list-sdks"], args.timeout)
        installed = re.findall(r"^(\d+\.\d+\.\d+)\s+\[([^\r\n]+)\]\s*$", sdk_text, re.MULTILINE)
        choices = [(version, directory) for version, directory in installed
                   if int(version.split(".")[0]) >= 8
                   and (args.sdk_version is None or version == args.sdk_version)]
        if not choices:
            raise ValueError("Requested stable SDK unavailable; .NET SDK 8+ is required")
        version, sdk_base = max(choices, key=lambda item: tuple(map(int, item[0].split("."))))
        for assembly in ("Microsoft.CodeAnalysis.dll", "Microsoft.CodeAnalysis.CSharp.dll"):
            if not (Path(sdk_base) / version / "Roslyn" / "bincore" / assembly).is_file():
                raise ValueError("Installed SDK does not contain required Roslyn assemblies")
        framework = "net" + version.split(".")[0] + ".0"
        (run_dir / "global.json").write_text(json.dumps({"sdk": {"version": version,
            "rollForward": "disable", "allowPrerelease": False}}), encoding="utf-8")
        (run_dir / "NuGet.Config").write_text(
            '<configuration><packageSources><clear /></packageSources></configuration>', encoding="utf-8")
        project = (support / "comment-syntax" / "CommentSyntax.csproj").read_text(encoding="utf-8")
        (run_dir / "CommentSyntax.csproj").write_text(project.replace("__FRAMEWORK__", framework), encoding="utf-8")
        shutil.copyfile(support / "comment-syntax" / "Program.cs", run_dir / "Program.cs")
        (run_dir / "input.json").write_text(json.dumps({"sources": sources,
            "languageVersion": args.language_version, "symbols": args.define}), encoding="utf-8")
        properties = ["-p:ImportDirectoryBuildProps=false", "-p:ImportDirectoryBuildTargets=false",
                      "-p:ImportDirectoryPackagesProps=false", "-p:NuGetAudit=false",
                      "-p:UseSharedCompilation=false", "-p:MSBuildEnableWorkloadResolver=false",
                      "-nodeReuse:false"]
        run_command(runner, run_dir, [dotnet, "restore", "CommentSyntax.csproj", "--configfile",
                    str(run_dir / "NuGet.Config"), "--disable-parallel", *properties], args.timeout)
        run_command(runner, run_dir, [dotnet, "build", "CommentSyntax.csproj", "--no-restore",
                    "--disable-build-servers", "--configuration", "Release", *properties], args.timeout)
        output = run_command(runner, run_dir, [dotnet, str(run_dir / "bin" / "Release" /
                             framework / "CommentSyntax.dll"), str(run_dir / "input.json")], args.timeout)
        result = json.loads(output)
        report.update(result, scope=str(scope), sdk_version=version, framework=framework,
                      files=[str(path) for path in files], scanned_files=len(files))
        exit_code = 0 if report["status"] == "completed" else 3
    except (OSError, ValueError, AttributeError, ImportError) as error:
        report.update(status="error", error=str(error))
    if run_dir is not None:
        try:
            (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as error:
            report.update(status="error", error="Cannot retain report: " + str(error))
            exit_code = 2
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
