using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;
using Microsoft.CodeAnalysis.Text;

var input = JsonDocument.Parse(File.ReadAllText(args[0], Encoding.UTF8)).RootElement;
if (!LanguageVersionFacts.TryParse(input.GetProperty("languageVersion").GetString(), out var language))
{
    throw new ArgumentException("Unrecognized C# language version");
}

var symbols = input.GetProperty("symbols").EnumerateArray().Select(value => value.GetString()!).ToArray();
if (symbols.Any(symbol => !SyntaxFacts.IsValidIdentifier(symbol) || symbol.StartsWith('@')))
{
    throw new ArgumentException("Preprocessor symbols must be plain C# identifiers");
}

var options = new CSharpParseOptions(language, DocumentationMode.Diagnose, SourceCodeKind.Regular, symbols);
var findings = new List<object>();
var diagnostics = new List<object>();
var uncheckedRegions = new List<object>();
var seen = new HashSet<(string, string, int)>();
var periodPattern = new Regex(@"(?:。|(?<!\.)\.)(?:\s*</[A-Za-z_][\w:.-]*\s*>)*\s*(?:\*/)?\s*$", RegexOptions.CultureInvariant);
var cdataPeriodPattern = new Regex(@"(?:。|(?<!\.)\.)\s*$", RegexOptions.CultureInvariant);

foreach (var item in input.GetProperty("sources").EnumerateArray())
{
    var path = item.GetProperty("path").GetString()!;
    var source = SourceText.From(item.GetProperty("text").GetString()!, Encoding.UTF8);
    var tree = CSharpSyntaxTree.ParseText(source, options, path);
    foreach (var diagnostic in tree.GetDiagnostics())
    {
        var position = source.Lines.GetLinePosition(diagnostic.Location.SourceSpan.Start);
        diagnostics.Add(new { file = path, line = position.Line + 1, column = position.Character + 1,
            id = diagnostic.Id, severity = diagnostic.Severity.ToString(), message = diagnostic.GetMessage(CultureInfo.InvariantCulture) });
    }

    foreach (var trivia in tree.GetRoot().DescendantTrivia(descendIntoTrivia: true))
    {
        if (trivia.IsKind(SyntaxKind.DisabledTextTrivia))
        {
            var start = source.Lines.GetLinePosition(trivia.SpanStart);
            var end = source.Lines.GetLinePosition(Math.Max(trivia.SpanStart, trivia.Span.End - 1));
            uncheckedRegions.Add(new { file = path, start_line = start.Line + 1, end_line = end.Line + 1,
                reason = "Inactive preprocessor branch; rerun with the applicable --define symbols" });
            continue;
        }

        var documentation = trivia.GetStructure() as DocumentationCommentTriviaSyntax;
        if (documentation is null && !trivia.IsKind(SyntaxKind.SingleLineCommentTrivia)
            && !trivia.IsKind(SyntaxKind.MultiLineCommentTrivia))
        {
            continue;
        }

        var cdataBodies = new List<TextSpan>();
        var closingStructure = new List<TextSpan>();
        if (documentation is not null)
        {
            closingStructure.Add(documentation.EndOfComment.FullSpan);
            foreach (var node in documentation.DescendantNodes())
            {
                if (node is XmlCDataSectionSyntax cdata && !cdata.EndCDataToken.IsMissing)
                {
                    cdataBodies.Add(TextSpan.FromBounds(cdata.StartCDataToken.Span.End, cdata.EndCDataToken.SpanStart));
                    closingStructure.Add(cdata.EndCDataToken.Span);
                }
                else if (node is XmlElementEndTagSyntax endTag)
                {
                    closingStructure.Add(endTag.Span);
                }

                XmlNameSyntax? name = node switch
                {
                    XmlElementStartTagSyntax tag => tag.Name,
                    XmlElementEndTagSyntax tag => tag.Name,
                    XmlEmptyElementSyntax tag => tag.Name,
                    _ => null,
                };
                if (name is null || name.Prefix is not null || name.LocalName.ValueText != "summary")
                {
                    continue;
                }

                var physicalLine = source.Lines.GetLineFromPosition(node.SpanStart);
                var payload = physicalLine.ToString().Trim();
                var documentationLine = payload.StartsWith("///", StringComparison.Ordinal)
                    && !payload.StartsWith("////", StringComparison.Ordinal);
                if (documentationLine)
                {
                    payload = payload[3..].Trim();
                }

                if (!documentationLine || payload != source.ToString(node.Span)
                    || node is XmlEmptyElementSyntax || node.Span.End > physicalLine.End)
                {
                    AddFinding("summary_inline", "syntax_layout_violation", node.SpanStart,
                        "Real documentation summary tag does not occupy its own physical /// comment line");
                }
            }
        }

        closingStructure.Sort((left, right) => left.Start.CompareTo(right.Start));
        var closingIndex = 0;
        var cdataBodyIndex = 0;
        var firstLine = source.Lines.GetLineFromPosition(trivia.FullSpan.Start).LineNumber;
        var lastLine = source.Lines.GetLineFromPosition(Math.Max(trivia.FullSpan.Start, trivia.FullSpan.End - 1)).LineNumber;
        for (var number = firstLine; number <= lastLine; number++)
        {
            var physicalLine = source.Lines[number];
            var start = Math.Max(physicalLine.Start, trivia.FullSpan.Start);
            var end = Math.Min(physicalLine.End, trivia.FullSpan.End);
            var snippet = source.ToString(TextSpan.FromBounds(start, Math.Max(start, end)));
            var match = periodPattern.Match(snippet);
            if (!match.Success && cdataBodies.Count > 0)
            {
                // Mask only syntax-owned endings without shifting the original UTF-16 positions
                var suffixView = snippet.ToCharArray();
                while (closingIndex < closingStructure.Count && closingStructure[closingIndex].End <= start)
                {
                    closingIndex++;
                }

                for (var index = closingIndex; index < closingStructure.Count && closingStructure[index].Start < end; index++)
                {
                    var span = closingStructure[index];
                    var maskStart = Math.Max(start, span.Start);
                    var maskEnd = Math.Min(end, span.End);
                    if (maskStart < maskEnd)
                    {
                        Array.Fill(suffixView, ' ', maskStart - start, maskEnd - maskStart);
                    }
                }

                var cdataMatch = cdataPeriodPattern.Match(new string(suffixView));
                if (cdataMatch.Success)
                {
                    var offset = start + cdataMatch.Index;
                    while (cdataBodyIndex < cdataBodies.Count && cdataBodies[cdataBodyIndex].End <= offset)
                    {
                        cdataBodyIndex++;
                    }

                    if (cdataBodyIndex < cdataBodies.Count && cdataBodies[cdataBodyIndex].Contains(offset))
                    {
                        match = cdataMatch;
                    }
                }
            }

            if (match.Success)
            {
                AddFinding("comment_terminal_period", "candidate", start + match.Index,
                    "Real comment text ends with a period; review abbreviations, URLs, code and literal data before editing");
            }
        }
    }

    void AddFinding(string rule, string classification, int offset, string message)
    {
        if (!seen.Add((path, rule, offset)))
        {
            return;
        }

        var position = source.Lines.GetLinePosition(offset);
        findings.Add(new { file = path, line = position.Line + 1, column = position.Character + 1,
            offset, rule, classification, message, text = source.Lines[position.Line].ToString() });
    }
}

Console.OutputEncoding = new UTF8Encoding(false);
Console.WriteLine(JsonSerializer.Serialize(new
{
    status = diagnostics.Count > 0 || uncheckedRegions.Count > 0 ? "incomplete" : "completed",
    language_version = options.LanguageVersion.ToDisplayString(),
    symbols,
    coordinate_unit = "one-based physical lines and UTF-16 columns; offset is zero-based UTF-16",
    findings,
    diagnostics,
    unchecked_regions = uncheckedRegions,
    selected_rules = new[] { "summary_inline", "comment_terminal_period" },
    limitations = new[]
    {
        "Syntax only: no project evaluation, compilation, semantic summary review or automatic edits",
        "Only active branches for the supplied symbols are scanned; project symbols are not inferred",
        "Period matches remain review candidates, including prose/code examples and literal data",
        "A zero finding count is not proof of overall compliance; other regex rules are not replaced",
    },
}));
