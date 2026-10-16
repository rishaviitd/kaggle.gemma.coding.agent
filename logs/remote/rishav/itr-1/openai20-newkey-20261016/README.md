# OpenAI model runs — 20 validation tasks

Same src/rishav/itr-1 agent; low reasoning effort; 40 tool calls; 5.5 minutes per task; 8,192 output tokens per model request. Responses API was used with provider-specific temporary configuration.

| Model | Solved | Not solved | Execution errors included in not solved |
|---|---:|---:|---:|
| gpt-6-luna | 11/20 | 9 | 1 |
| gpt-6.1-sol | 15/20 | 5 | 0 |
| gpt-6-astra | 16/20 | 4 | 0 |

## Trace provenance

model_trace.json is a website-readable view of actual recorded ATIF agent events. These events may split a single API response across multiple events. Matching per-call request payloads retain their original Responses API input and add derived messages for formatting. model_trace_capture.json preserves the original per-call capture, including incomplete/error turns. provider_audit.json records API response IDs, usage and returned reasoning summaries; traces/ contains the original ATIF events. agent_trace.json is derived from the website-readable view. No task was rerun when preparing publication.

The original trace exporter did not parse Responses API outputs. The website views restore supported content from the recorded ATIF events and provider audits; they do not synthesize missing reasoning or malformed tool arguments that were not retained.
