import { describeCron, shiftOf } from "../cron";
import "./shift.css";

interface ShiftJob {
  name: string;
  label: string;
  cron: string;
  enabled: boolean;
}

/** An agent's working day on a 24-hour track.
 *
 * Every mark is a real scheduled run, read straight off the cron: a filled tick for a daily
 * pass, a hollow one for a weekly pass, a band for something that runs right through the day.
 * Paused passes stay visible but drawn back, so a quiet agent looks quiet rather than empty. */
export function ShiftStrip({ jobs, hours = [0, 6, 12, 18] }: { jobs: ShiftJob[]; hours?: number[] }) {
  if (!jobs.length) return null;
  return (
    <div className="shift" aria-hidden="true">
      <div className="shift-track">
        {hours.map((h) => (
          <i className="shift-hour" key={h} style={{ left: `${(h / 24) * 100}%` }} />
        ))}
        {jobs.map((job) => {
          const s = shiftOf(job.cron);
          const cls = `shift-mark${job.enabled ? "" : " off"}${s.weekly ? " weekly" : ""}`;
          const title = `${job.label} — ${describeCron(job.cron)}`;
          if (s.kind === "band")
            return <i className={`${cls} band`} key={job.name} title={title} />;
          return s.hours.map((h) => (
            <i
              className={cls}
              key={`${job.name}-${h}`}
              title={title}
              style={{ left: `${(h / 24) * 100}%` }}
            />
          ));
        })}
      </div>
      <div className="shift-scale">
        {hours.map((h) => (
          <span key={h} style={{ left: `${(h / 24) * 100}%` }}>
            {String(h).padStart(2, "0")}
          </span>
        ))}
        <span className="shift-end">24</span>
      </div>
    </div>
  );
}
