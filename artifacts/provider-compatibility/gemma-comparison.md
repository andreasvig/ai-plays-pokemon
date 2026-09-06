# Gemma profile comparison

Both profiles passed the recorded-screenshot protocol test on 2026-09-06. Each used three action requests, one handover and a checkpoint reload. No emulator actions were executed.

| Profile | Turn 2 raw reasoning replay | Protocol | Reported cost | Trace |
|---|---|---|---:|---|
| gemma-guidance | 0 blocks sent; 2 archived | Pass | $0.00093673 | [Open](http://localhost:3420/history/2026-09-06_10-38-33_protocol-probe__google--gemma-4-31b-it__gemma-guidance) |
| gemma-replay | 2 blocks sent; 2 archived | Pass | $0.00113342 | [Open](http://localhost:3420/history/2026-09-06_10-38-34_protocol-probe__google--gemma-4-31b-it__gemma-replay) |

Same-history request audit: after removing earlier raw reasoning fields and using the same session ID, the outbound request bodies were identical. Endpoint, sampling, output instructions, cache strategy, thinking setting, tools/schema, and compaction prompts are shared.

Both profiles intentionally reset old raw thinking after compaction. All original responses are kept in the archive, including the guidance variant. The selected profile is saved with the run; resume cannot switch profiles.

This is not a gameplay-performance comparison. Provider acceptance and a locally intact replay do not prove the host used earlier raw thinking. No winner is established.

Use `--provider-profile gemma-guidance` or `--provider-profile gemma-replay` with `--config configs/config-append.yaml --model google/gemma-4-31b-it`. The default remains omission of prior raw thinking.

Cache reads in these probes: gemma-guidance: 60.9%, gemma-replay: 0.0%. These short runs may reuse prefixes from earlier probes; this is not a controlled cache-performance comparison. Gemma cache reuse is now observed on the guidance route, unlike the earlier short probe.
