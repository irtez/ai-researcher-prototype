# Modern Notebook UI

For the Claude Code deployment used on this branch, follow
[Local Claude Code + UI](operations/claude-local.md). It uses a host runner
and does not start the old CodeArkt server. The UI defaults to Claude Code;
the older setup below requires selecting its matching orchestrator explicitly.

The notebook UI is a read-mostly research workbench for existing Holosophus
sessions in `workdir/<session_id>/`. It runs beside the existing CodeArkt agent
server and does not replace the terminal or Gradio clients.

## Results and controls

The iteration selector, saved results and message history are described in
[Research iterations](operations/research-iterations.md). Feedback starts with
an expandable button; the current approval question remains visible while
browsing history or keeping the feedback editor closed.
When the pending question card is outside the viewport, a fixed notice links
back to it. New questions do not switch the selected agent/iteration or scroll
the reader automatically. “Go to request” scrolls to and focuses the current
card; the notice disappears when the card is visible or the request resolves.
Inside “Request, plans and decisions”, the user's request (or feedback) and
the manager's final message each keep their heading and body in one bordered
card. Planning documents and decision history retain their own disclosures.
Submitting feedback is acknowledged when the runner accepts the HTTP request,
before the model's first response. The form closes, iteration history refreshes
immediately, and an empty running feed says it is waiting for the manager.
Rejected requests keep the draft; a login retry preserves the acceptance callback.
`ui/tests/feedback-submit-browser.py` checks submission, delayed output, refusal
and optional `UI_TEST_REAUTH=1` login retry against a local fake stream, without LLM.

Internal `.holosophus_ctl` files are excluded from ordinary artifact lists,
counts and pinned cards. Saved plans and research files remain accessible.
The task label uses the session's explicit mode; intermediate files and input
PDFs do not imply a full-paper task. Ordinary sessions display “Research”.

Capacity comes from the old CodeArkt dispatcher. The UI displays and polls it
only when CodeArkt is selected or identified as the session's orchestrator.
Claude Code hides this indicator and does not poll `/capacity`; cached capacity
from a previously selected orchestrator is not displayed.

The standalone GPU monitoring button is removed from both header states.
Compute selection and server-side GPU configuration remain available.

## Input attachments

The interface is Russian-only, including when an old browser preference
contains `en`. The profile labels are «Компьютерные науки» (`general`) and
«Химия» (`chemistry`). The form always starts an ordinary research task and
has no debug-mode selector. Compute choices describe placement: automatic,
local Docker on the application server, or a configured remote host.
The wire values remain `Auto`, `Local`, and `GPU`. Local GPU access is configured
separately with `LOCAL_GPU_DEVICES`, as described in
[Docker Bash](operations/docker-bash.md).

Four task examples appear below the prompt field. Selecting one fills an
editable task and its input files, selects the matching profile, and preserves
compatible model/effort/compute settings and approval choices. Replacing a
nonempty draft or attachments requires confirmation. It does not start a run.
The laboratory example includes three small synthetic CSV files with calibration
replicates, samples/dilution factors and quality controls. The molecule comparison
and the two paper examples (machine learning and chemistry) require no uploads.
Cards use two columns on desktop and one on mobile. The show/hide choice is saved
in `holosophus.ui.examplesCollapsed`; examples remain usable if storage fails.

Example content lives in `ui/src/launchExamples.ts`, presentation in
`ui/src/LaunchExamples.tsx`. Changes to a prompt or input require rerunning
that example in the later demo validation, separate from the UI checks.
The pending research checks are recorded in the
[demo plan](plans/2026-10-08-demo-launch-ui.md#later-demo-validation-required-not-completed-here).

Before starting a Claude Code task, both profiles offer the same
attachment picker. Switching profiles preserves the selected files. Uploads
finish before the manager starts; a failed upload keeps the form available for
correction and prevents the model request. Originals are saved in the session's
`inputs/` directory and their paths are included in the manager's task.
Supported formats and upload limits are listed in
[Session files](operations/chemistry-local.md#файлы-сессии).
The feedback form offers the same picker after an iteration finishes. New files
are saved as separate versions and listed with the corresponding iteration;
previous inputs and results remain available. If sources approval is enabled,
new attachments require a new sources decision. See
[Feedback attachments](operations/research-iterations.md#вложения-при-доработке).

`ui/tests/launch-readiness-browser.py` tests profile switching, upload failure
and retry, file delivery and delayed admission against the real UI/upload API
with a fake manager. Run it with `UI_TEST_PROFILE=general` and `chemistry`;
neither variant calls an LLM.

`ui/tests/demo-launch-browser.py` checks Russian UI, example visibility,
safe draft replacement, settings preservation, and actual file uploads with
a local stub manager. It also checks a narrow viewport and unavailable
localStorage. Build the UI first, then run from the integration root:

```bash
uv run --no-sync --with playwright==1.49.1 python ui/tests/demo-launch-browser.py
```

The browser check uses the Chromium installed for that Playwright version,
or the executable selected by `UI_TEST_CHROMIUM`. It does not call a model.

## Local Development

Terminal 1:

```bash
cd Holosophus
uv run --project holosophos python -m holosophos.ui_api --host 127.0.0.1 --port 5058
```

Terminal 2:

```bash
cd Holosophus/ui
pnpm install
pnpm dev -- --host 127.0.0.1 --port 5173
```

Open:

```text
http://127.0.0.1:5173
```

The Vite dev server proxies:

- `/ui/*` to `http://127.0.0.1:5058`
- `/agents/*` to `http://127.0.0.1:5055`

## Docker Profile

```bash
cd Holosophus
docker compose --profile ui up --build -d
```

Open:

```text
http://127.0.0.1:5062
```

The `ui-api` service mounts `./workdir:/workdir:ro`. The browser never receives
API keys or `.env` content.

## Agent activity and preview

The production Run Details view uses `src/activity/ActivityFeed.tsx` and the
`activity/fromEvents.ts` adapter for live/persisted research records. The manager
view includes iteration context; each agent invocation has its own tab. Old
records without call identities are explicitly grouped by role; missing tool
responses and launch boundaries are not inferred. Full event payloads come from
server history, not the compact browser cache. Both research profiles share this view.

The same component can also be viewed with synthetic events, without a backend or model:

```bash
cd Holosophus/ui
npm run dev -- --host 127.0.0.1 --port 5174 --strictPort
```

Open `http://127.0.0.1:5174/tests/activity-preview.html`. The playback controls
feed synthetic events to `src/activity/ActivityFeed.tsx`, using the app's styles
and Markdown renderer. Manager messages contain compact delegation cards;
each invocation opens in its own tab. Tabs append in launch order and never
reorder on completion. Navigation is manual, with scroll positions and tool
disclosures preserved. Tool responses are collapsed by default, and failures
are visible in the call summary.
Text messages use white cards with a subtle border and an indigo author accent
to distinguish them from compact tool rows.

The preview entry
under `tests/` is not part of the normal production build. It performs no API
requests and does not alter research sessions. The browser check is
`ui/tests/activity-preview-browser.py` and requires the dev server above.
`ui/tests/iteration-history-browser.py` checks the actual compiled UI and history
API with SDK message fixtures: repeated roles, completion order, collapsed
responses, visible errors, navigation, legacy history and mobile width; no LLM.

## Validation

Backend:

```bash
cd Holosophus/holosophos
uv run pytest tests/test_ui_api.py -q
```

Frontend:

```bash
cd Holosophus/ui
pnpm build
```

Compose syntax:

```bash
cd Holosophus
docker compose --profile ui config --quiet
```

Manual UI checks:

1. Open an existing completed session.
2. Switch between Brief, Experiments, Paper, Review, and Run Details.
3. Open PDF, markdown, JSON, CSV, image, and code artifacts from the drawer.
4. Confirm the bottom artifact strip remains compact by default.
5. Check a narrow viewport around 390px width.

Live run smoke, when the CodeArkt stack is already configured:

```bash
cd Holosophus
docker compose up --build -d
python scripts/smoke_test_pipeline.py
```

Then open the notebook UI and start a small debug run from the task composer.
The active agent, event timeline, and artifacts should update without reload.
