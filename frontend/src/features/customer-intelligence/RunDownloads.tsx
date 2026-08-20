interface RunDownloadsProps {
  urls: { json: string; markdown: string } | null;
}

export function RunDownloads({ urls }: RunDownloadsProps) {
  if (!urls) return null;
  return (
    <nav className="run-downloads" aria-label="Run 다운로드">
      <a href={urls.json} download>JSON 다운로드</a>
      <a href={urls.markdown} download>Markdown 다운로드</a>
    </nav>
  );
}
