<!-- Privileged-information probe (ahd M3.2, no paper counterpart): what can a blind model
recover about the task from one rendered diagnosis alone? It sees the diagnosis, the task list
of the mining pool with one-line descriptions, the source's tool vocabulary and the answer
categories; never a trajectory. Cached, ledgered as arm "probe". Output JSON only. -->
You see one diagnosis of a failed agent run (identifiers removed). You do not see the run.

Diagnosis:
{diagnosis}

The run was on exactly one of these tasks (id: one-line description):
{tasks}

Tools available in this harness:
{tools}

Answer categories in use:
{categories}

Answer with a single JSON object:
{"task_top3": ["<task id>", "<task id>", "<task id>"], "required_tools": ["<tool name>", "..."], "answer_category": "<one of the categories>"}
"task_top3" is ordered from most to least likely; "required_tools" are the tool calls needed to
solve the task you ranked first, using names from the tool list only.
