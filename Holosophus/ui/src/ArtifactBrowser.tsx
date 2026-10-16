import { useMemo, useState } from "react";
import { ChevronRight, FileText, FileCode2, FileImage, FileJson, FolderOpen, Search } from "lucide-react";
import type { Artifact } from "./types";
import { buildArtifactTree, findArtifacts, folderLabelKeys, isMarkdown, markdownDocuments } from "./artifacts";
import type { ArtifactFolder } from "./artifacts";
import { t } from "./i18n";

const PAGE_SIZE = 40;

export function ArtifactBrowser({ artifacts, onOpenArtifact, search: externalSearch, showSearch = true }: {
  artifacts: Artifact[];
  onOpenArtifact: (artifact: Artifact) => void;
  search?: string;
  showSearch?: boolean;
}) {
  const [search, setSearch] = useState("");
  const query = (externalSearch ?? search).trim();
  const files = useMemo(() => findArtifacts(artifacts, query), [artifacts, query]);
  const tree = useMemo(() => buildArtifactTree(files), [files]);
  const documents = useMemo(() => markdownDocuments(files), [files]);
  return (
    <section className="artifact-browser" aria-label={t("files.title")}>
      <div className="artifact-browser-head">
        <strong>{t("files.title")} <small>{files.length}{query ? ` / ${artifacts.length}` : ""}</small></strong>
        {showSearch && <label className="search-box">
          <Search size={15} aria-hidden="true" />
          <input aria-label={t("drawer.searchArtifacts")} placeholder={t("drawer.searchArtifacts")}
            value={search} onChange={event => setSearch(event.target.value)} />
        </label>}
      </div>
      {!query && documents.length > 0 && <div className="artifact-documents">
        <span className="artifact-documents-label">{t("files.documents")}</span>
        {documents.map(file => <FileRow key={file.path} file={file} onOpen={onOpenArtifact} />)}
      </div>}
      {files.length === 0 ? <div className="empty-compact">{t(query ? "files.noMatches" : "artifacts.emptyTab")}</div> :
        <div className="artifact-tree-scroll" tabIndex={0} aria-label={t("files.tree")}>
          <FolderContents key={query} folder={tree} searching={Boolean(query)} onOpen={onOpenArtifact} />
        </div>}
    </section>
  );
}

function FolderContents({ folder, searching, onOpen }: { folder: ArtifactFolder; searching: boolean; onOpen: (file: Artifact) => void }) {
  const [limit, setLimit] = useState(PAGE_SIZE);
  // Both folders and files are paged; closed subtrees are never mounted.
  const items = [...folder.folders.map(value => ({ folder: value, file: null })),
    ...folder.files.map(value => ({ folder: null, file: value }))];
  return <div className="artifact-tree-contents">
    {items.slice(0, limit).map(item => item.folder
      ? <FolderRow key={item.folder.path} folder={item.folder} searching={searching} onOpen={onOpen} />
      : <FileRow key={item.file!.path} file={item.file!} onOpen={onOpen} />)}
    {items.length > limit && <button className="artifact-more" onClick={() => setLimit(limit + PAGE_SIZE)}>
      {t("files.more", { n: Math.min(PAGE_SIZE, items.length - limit), remaining: items.length - limit })}
    </button>}
  </div>;
}

function FolderRow({ folder, searching, onOpen }: { folder: ArtifactFolder; searching: boolean; onOpen: (file: Artifact) => void }) {
  const [open, setOpen] = useState(searching);
  const labelKey = folderLabelKeys[folder.path];
  return <div className="artifact-folder">
    <button className="artifact-folder-toggle" aria-expanded={open} onClick={() => setOpen(!open)} title={folder.path}>
      <ChevronRight size={15} className={open ? "folder-chevron open" : "folder-chevron"} aria-hidden="true" />
      <FolderOpen size={17} aria-hidden="true" />
      <span className="artifact-file-name">{labelKey ? t(labelKey) : folder.name}<small>{folder.path}/</small></span>
      <span className="artifact-file-size">{folder.count}</span>
    </button>
    {open && <FolderContents folder={folder} searching={searching} onOpen={onOpen} />}
  </div>;
}

function FileRow({ file, onOpen }: { file: Artifact; onOpen: (file: Artifact) => void }) {
  const Icon = file.preview_kind === "image" ? FileImage : /json/.test(file.preview_kind) ? FileJson :
    file.type.includes("code") ? FileCode2 : FileText;
  const size = file.size_bytes < 1024 ? `${file.size_bytes} B` : file.size_bytes < 1048576
    ? `${(file.size_bytes / 1024).toFixed(1)} KB` : `${(file.size_bytes / 1048576).toFixed(1)} MB`;
  return <button className={`artifact-file ${isMarkdown(file) ? "markdown-file" : ""}`} onClick={() => onOpen(file)} title={file.path}>
    <Icon size={16} aria-hidden="true" />
    <span className="artifact-file-name">{file.name}<small>{file.path}</small></span>
    <span className="artifact-file-size">{size}</span>
  </button>;
}
