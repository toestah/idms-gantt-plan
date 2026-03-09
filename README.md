# Gantt Chart Generator for draw.io

A Python script that generates swimlane Gantt chart diagrams from a simple JSON file. The output is a `.drawio` file you can open in [draw.io](https://app.diagrams.net/) (also known as diagrams.net).

## Requirements

- Python 3.10+
- No external dependencies — uses only the Python standard library.

## Quick Start

1. Create a JSON file describing your project (see **JSON Structure** below).
2. Run the generator:

   ```bash
   python generate_drawio.py your_project.json -o your_chart.drawio
   ```

3. Open the resulting `.drawio` file in [draw.io](https://app.diagrams.net/) (desktop app or web).

## JSON Structure

Your JSON file has the following top-level sections. See `schema.json` for the full specification, or `idms/idms.json` for a working example.

### `project` (required)

Defines the timeline window for your chart.

```json
{
  "project": {
    "name": "My Project",
    "start_date": "2025-06-01",
    "end_date": "2025-12-31",
    "time_unit": "week"
  }
}
```

- `start_date` / `end_date` — ISO 8601 dates (`YYYY-MM-DD`) that define the visible timeline range.
- `time_unit` — `"week"` or `"month"`. Controls the column granularity on the timeline header.

### `workstreams`

Workstreams become the **swimlane rows** in your chart. Each task and milestone must belong to one.

```json
"workstreams": [
  { "id": "design", "name": "Design", "color": "#9C27B0" },
  { "id": "backend", "name": "Backend", "color": "#4285F4" },
  { "id": "frontend", "name": "Frontend", "color": "#34A853" }
]
```

- `id` — A short identifier referenced by tasks/milestones.
- `name` — The label shown on the chart.
- `color` — Hex color for the swimlane header and task bars.

### `tasks`

Tasks are the horizontal bars on the chart.

```json
"tasks": [
  {
    "id": "t1",
    "name": "Build API",
    "workstream": "backend",
    "start": "2025-06-01",
    "end": "2025-07-15",
    "owner": "Alice",
    "status": "in_progress",
    "percent_complete": 40
  }
]
```

- `id`, `name`, `workstream`, `start`, `end` — Required.
- `owner` — Shown on the task bar (optional).
- `status` — Controls the bar color. One of: `not_started`, `in_progress`, `complete`, `on_track`, `at_risk`, `delayed`, `blocked`.
- `percent_complete` — `0`–`100`. Shown as a progress fill inside the bar (optional).

### `milestones`

Milestones appear as diamond markers at a specific date within a workstream.

```json
"milestones": [
  {
    "id": "m1",
    "name": "Beta Release",
    "date": "2025-08-01",
    "workstream": "frontend",
    "status": "on_track"
  }
]
```

### `dependencies`

Draws arrows between tasks and/or milestones to show sequencing.

```json
"dependencies": [
  { "from": "t1", "to": "t2", "type": "finish_to_start" }
]
```

- `type` — One of: `finish_to_start` (default), `start_to_start`, `finish_to_finish`, `start_to_finish`.

### `date_markers`

Vertical lines drawn across the entire chart to highlight key dates (e.g., deadlines, review gates).

```json
"date_markers": [
  {
    "id": "dm1",
    "name": "Go/No-Go Decision",
    "date": "2025-09-15",
    "color": "#FF0000",
    "style": "dashed"
  }
]
```

### `risks`

Risks are rendered as a summary table below the Gantt chart.

```json
"risks": [
  {
    "id": "r1",
    "name": "Vendor delay",
    "description": "Third-party API may not be ready on time.",
    "severity": "high",
    "likelihood": "medium",
    "status": "monitoring",
    "mitigation": "Prepare fallback implementation.",
    "affected": ["t1", "m1"]
  }
]
```

## Minimal Working Example

Save this as `example.json` and run `python generate_drawio.py example.json -o example.drawio`:

```json
{
  "project": {
    "name": "Example Project",
    "start_date": "2025-07-01",
    "end_date": "2025-09-30",
    "time_unit": "week"
  },
  "workstreams": [
    { "id": "dev", "name": "Development", "color": "#4285F4" },
    { "id": "qa", "name": "QA", "color": "#34A853" }
  ],
  "tasks": [
    { "id": "t1", "name": "Build feature", "workstream": "dev", "start": "2025-07-01", "end": "2025-08-15", "status": "not_started" },
    { "id": "t2", "name": "Write tests", "workstream": "qa", "start": "2025-08-01", "end": "2025-08-31", "status": "not_started" }
  ],
  "dependencies": [
    { "from": "t1", "to": "t2" }
  ],
  "milestones": [
    { "id": "m1", "name": "Release", "date": "2025-09-15", "workstream": "dev", "status": "on_track" }
  ]
}
```

## Viewing and Exporting Your Chart

After running the generator, you'll have a `.drawio` file. To view it:

1. Go to [app.diagrams.net](https://app.diagrams.net/) in your browser (no account required).
2. Choose **Open Existing Diagram** and select your `.drawio` file.
3. Your Gantt chart will render in the editor, where you can pan, zoom, and inspect it.

To export to PDF or an image:

1. In draw.io, go to **File > Export as > PDF** (or PNG, SVG, etc.).
2. Adjust page settings as needed — for wide Gantt charts, landscape orientation and "Fit to page" work well.
3. Click **Export** and save.

You can also install the [draw.io desktop app](https://github.com/jgraph/drawio-desktop/releases) if you prefer to work offline.

## Updating Your Chart with AI

You don't need to hand-edit the JSON file. You can use an AI coding assistant like [Gemini CLI](https://github.com/google-gemini/gemini-cli) to make updates conversationally. For example:

```bash
gemini
```

Then ask it something like:

> "Add a new task called 'Security Audit' to the 'qa' workstream, starting Aug 15 and ending Sep 1, and make it depend on task t1."

The included `schema.json` file helps the AI understand the exact structure and valid values for your JSON — point the AI to it for best results. For example, you can tell it:

> "Read schema.json and my_project.json, then help me add a new workstream with three tasks."

After the AI updates your `.json` file, just re-run the generator to produce an updated `.drawio`:

```bash
python generate_drawio.py your_project.json -o your_chart.drawio
```

## Tips

- **Overlapping tasks** within the same workstream are automatically stacked vertically.
- **Task bar colors** are driven by `status`, not by the workstream color, so you can see progress at a glance.
- The `idms/` folder contains a full example you can reference — just note that the content is specific to that project. Replace it with your own data.
- You can validate your JSON against `schema.json` with any JSON Schema validator.
