import assert from "node:assert/strict";
import test from "node:test";
import { buildArtifactTree, findArtifacts, markdownDocuments, paperPdf, hasPaper } from "../src/artifacts.ts";
import type { Artifact } from "../src/types.ts";

const artifact = (path: string): Artifact => ({ path, name: path.split("/").at(-1)!, type: "artifact", tab: "run", size_bytes: 10, modified_at: "", preview_kind: "text", pinned: false });

test("paper preview never promotes arbitrary or nested PDFs to the article", () => {
  const files = ["graph.pdf", "test.pdf", "draft/paper.pdf", "inputs/paper.pdf"].map(artifact);
  assert.equal(paperPdf(files), undefined);
  const canonical = { ...artifact("paper.pdf"), modified_at: "2026-10-09T12:00:00Z" };
  assert.equal(paperPdf([...files, canonical]), canonical);
  const updated = { ...canonical, modified_at: "2026-10-09T12:30:00Z" };
  assert.equal(paperPdf([...files, updated])?.modified_at, "2026-10-09T12:30:00Z");
});

test("paper directory is recognized without promoting draft or experiment PDFs", () => {
  const paper = artifact("paper/paper.pdf");
  const files = [artifact("mle/results/plot.pdf"), artifact("draft/paper.pdf"), paper];
  assert.equal(paperPdf(files), paper);
  assert.equal(hasPaper(files), true);
  assert.equal(hasPaper([artifact("paper/paper.tex")]), true);
  assert.equal(hasPaper(files.slice(0, 2)), false);
  const root = artifact("paper.pdf");
  assert.equal(paperPdf([...files, root]), root);
});

test("registered article takes precedence over root PDFs and experiment classification", () => {
  const selected = artifact("mle/results/статья.pdf");
  const files = [artifact("paper.pdf"), artifact("paper/paper.pdf"), selected];
  const document = { registered: true as const, status: "checking" as const,
    tex_path: "mle/results/статья.tex", pdf_path: selected.path, pdf_available: true, error: null };
  assert.equal(paperPdf(files, document), selected);
  assert.equal(hasPaper(files, document), true);
});

test("a missing registered PDF exposes the paper tab without falling back to old PDFs", () => {
  const document = { registered: true as const, status: "checking" as const,
    tex_path: "results/article.tex", pdf_path: "results/article.pdf", pdf_available: false, error: null };
  assert.equal(hasPaper([], document), true);
  assert.equal(paperPdf([artifact("paper.pdf")], document), undefined);
  // Independently polled artifacts may still contain a PDF the summary found missing.
  assert.equal(paperPdf([artifact("results/article.pdf")], document), undefined);
});

test("invalid document registration stays local and prevents legacy article selection", () => {
  const document = { registered: true as const, status: "error" as const,
    tex_path: null, pdf_path: null, pdf_available: false, error: "Invalid registration" };
  assert.equal(hasPaper([], document), true);
  assert.equal(paperPdf([artifact("paper.pdf")], document), undefined);
  assert.equal(hasPaper([artifact("data.csv"), artifact("figures/chart.png")], null), false);
});

test("folder hierarchy preserves unknown research files and counts nested contents", () => {
  const files = ["mle/code/run.py", "mle/report.md", "mle/results/data.json", "unknown/data.xyz", "prompt.md"].map(artifact);
  const tree = buildArtifactTree(files);
  assert.equal(tree.count, 5);
  assert.deepEqual(tree.folders.map(f => [f.path, f.count]), [["mle", 3], ["unknown", 1]]);
  assert.equal(tree.folders[0].files[0].path, "mle/report.md");
  assert.equal(tree.folders[1].files[0].path, "unknown/data.xyz");
});

test("search finds nested paths case insensitively and retains ancestors", () => {
  const files = ["mle/results/Результат.md", "mle/code/run.py", "custom/data.xyz"].map(artifact);
  const hits = findArtifacts(files, "РЕЗУЛЬТАТ");
  assert.deepEqual(hits.map(f => f.path), ["mle/results/Результат.md"]);
  assert.equal(buildArtifactTree(hits).folders[0].folders[0].path, "mle/results");
  assert.equal(findArtifacts(files, "mle/").length, 2);
});

test("Markdown is prioritized without dropping other files; quick documents are bounded", () => {
  const files = ["z.markdown", "a.csv", "b.md", ...Array.from({length: 1000}, (_, i) => `notes/${i}.md`)].map(artifact);
  const tree = buildArtifactTree(files);
  assert.deepEqual(tree.files.map(f => f.name), ["b.md", "z.markdown", "a.csv"]);
  assert.equal(tree.count, 1003);
  assert.equal(markdownDocuments(files).length, 5);
  assert.equal(buildArtifactTree(files.slice().reverse()).count, tree.count);
});
