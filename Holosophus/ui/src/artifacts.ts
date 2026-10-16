import type { Artifact, PaperDocumentState } from "./types.ts";

export interface ArtifactFolder {
  path: string;
  name: string;
  count: number;
  files: Artifact[];
  folders: ArtifactFolder[];
}

export const isMarkdown = (file: Artifact) => /\.(md|markdown)$/i.test(file.path);
// A registration is authoritative even while its PDF is missing or unreadable.
// Keep root/paper defaults for sessions created before registration was available.
export const paperPdf = (files: Artifact[], document?: PaperDocumentState | null) => {
  if (document) return document.pdf_available
    ? files.find(file => file.path === document.pdf_path) : undefined;
  return ["paper.pdf", "paper/paper.pdf"].map(path => files.find(file => file.path === path)).find(Boolean);
};
export const hasPaper = (files: Artifact[], document?: PaperDocumentState | null) => Boolean(document) || Boolean(paperPdf(files)) ||
  files.some(file => file.path === "paper.tex" || file.path === "paper/paper.tex");
const compareFiles = (a: Artifact, b: Artifact) =>
  Number(isMarkdown(b)) - Number(isMarkdown(a)) || a.path.localeCompare(b.path);

export function findArtifacts(files: Artifact[], search: string): Artifact[] {
  const query = search.trim().toLocaleLowerCase();
  return files.filter(file => file.path.toLocaleLowerCase().includes(query));
}

export function markdownDocuments(files: Artifact[]): Artifact[] {
  return files.filter(isMarkdown).sort((a, b) =>
    Number(b.path.startsWith("results/")) - Number(a.path.startsWith("results/")) || compareFiles(a, b)
  ).slice(0, 5);
}

export function buildArtifactTree(files: Artifact[]): ArtifactFolder {
  const root: ArtifactFolder = { path: "", name: "", count: 0, files: [], folders: [] };
  const folders = new Map<string, ArtifactFolder>([["", root]]);
  for (const file of files) {
    let parent = root;
    parent.count++;
    for (const name of file.path.split("/").slice(0, -1)) {
      const path = parent.path ? `${parent.path}/${name}` : name;
      let folder = folders.get(path);
      if (!folder) {
        folder = { path, name, count: 0, files: [], folders: [] };
        folders.set(path, folder);
        parent.folders.push(folder);
      }
      folder.count++;
      parent = folder;
    }
    parent.files.push(file);
  }
  for (const folder of folders.values()) {
    folder.files.sort(compareFiles);
    folder.folders.sort((a, b) => a.name.localeCompare(b.name));
  }
  return root;
}

// Only directories with a system-defined purpose receive a translated label.
// Arbitrary directory names (including numbers) retain their actual names.
export const folderLabelKeys: Record<string, string> = {
  inputs: "files.inputs", results: "files.results", sources: "files.sources",
  plans: "files.plans", mle: "files.experiments", "mle/code": "files.code",
  "mle/results": "files.tables", "mle/summaries": "files.summaries",
  figures: "files.figures", paper: "files.paper",
};
