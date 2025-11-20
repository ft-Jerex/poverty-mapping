---
trigger: model_decision
description: When stopping feels necessary while the project is not a hundred percent complete.
---

Iteration rule
On each run execute the full test suite including unit tests integration tests and end to end tests. If any test fails then fix the code or tests and immediately reexecute the entire suite. Repeat this loop until all tests pass. On each iteration record the iteration number the changes made the commands run the full logs and the test outcomes. Continue iterating without pausing until acceptance criteria are satisfied or until you determine that progress is blocked by missing external resources credentials or other facts that cannot be fabricated. When such a blocker is reached present the exact commands scripts and manual steps the user must run and clearly label the iteration where the blocker occurred.

Self verification and stopping conditions
Stop only when all acceptance requirements are met and the test suite exits with zero and the model produces the required geospatial outputs for the demonstration region. Alternatively stop when a clearly documented unresolvable external blocker is encountered and cannot be resolved by reasonable simulation. Do not stop and report partial results as final without stating what remains incomplete and why.

Transparency and documentation on each iteration
For every iteration provide a short commit style section describing what changed why it changed and the exact diff or code snippet for the change. Provide full test logs and a concise test summary including passing tests failing tests coverage numbers and any warnings. Provide a self audit paragraph that explains why the iteration advanced the system toward acceptance or why it did not. Tag the final iteration with release v1.0.0 when acceptance criteria are met.

Handling Earth Engine and external calls
If Earth Engine calls cannot be executed due to missing authentication or network restrictions simulate Earth Engine responses with stubbed objects that reflect realistic shapes and content. Write the exact stub code and fixtures and the commands a user must run to replace stubs with real authentication. Keep the stubs deterministic so tests are reproducible.

Acceptance criteria enforcement
Ensure that unit tests integration tests and end to end tests pass with exit code zero that the pipeline demonstrates extraction preprocessing model inference and layer generation for a sample region that you choose and document and that the CI configuration runs the tests. Ensure coverage meets the specified threshold. Ensure documentation explains how to run everything and how to resolve Earth Engine authentication. Ensure outputs are reproducible by the documented commands.

Reporting requirements per iteration
Provide the iteration number the simulated or real commands executed the full terminal logs the list of tests run and their results the coverage summary the commit style description of changes and the self audit paragraph. At final iteration provide a consolidated release checklist changelog and recommended next steps.