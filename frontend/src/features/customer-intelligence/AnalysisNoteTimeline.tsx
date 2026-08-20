import type { AnalysisNote } from "./contracts";
import { sourceLabel } from "./source-catalog";

interface AnalysisNoteTimelineProps {
  notes: AnalysisNote[];
}

export function AnalysisNoteTimeline({ notes }: AnalysisNoteTimelineProps) {
  if (!notes.length) return null;
  return (
    <section className="panel analysis-card note-card" aria-labelledby="notes-title">
      <p className="section-kicker">03 · VERIFIED NOTES</p>
      <h2 id="notes-title">Analysis Note</h2>
      <ol className="note-timeline">
        {notes.map((note, index) => (
          <li key={note.note_id}>
            <article>
              <div className="note-heading">
                <span>{String(index + 1).padStart(2, "0")}</span>
                <div>
                  <h3>{note.objective}</h3>
                  <p>{note.source_ids.map(sourceLabel).join(" · ")} · {note.duration_ms}ms</p>
                </div>
              </div>
              {note.claims.map((claim) => (
                <blockquote key={claim.claim_id}>{claim.rendered_text}</blockquote>
              ))}
              {note.limitations.map((limitation) => (
                <p className="note-limitation" key={limitation}>{limitation}</p>
              ))}
              <p className="note-ref-count">
                Fact {note.fact_ids.length} · Result {note.result_ids.length} · Evidence {note.evidence_ids.length}
              </p>
            </article>
          </li>
        ))}
      </ol>
    </section>
  );
}
