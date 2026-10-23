---
name: design-eval
description: Design a small evaluation dataset and rubric for an LLM task or augmented workflow.
disable-model-invocation: true
---

# Design an evaluation

1. Read README.md and the existing case format. Ask for the task, target users,
   workflow boundaries, and acceptance criteria if these are unclear.
2. Define observable success, unacceptable failures, and an anchored review
   rubric before proposing test cases. Distinguish final-answer correctness,
   evidence quality, tool behavior, and operational constraints.
3. Propose representative development cases: a happy path, an edge case,
   missing evidence, conflicting evidence, and an adversarial instruction in
   retrieved context. Use only synthetic or approved sanitized data.
4. For executable cases, use unique string `id`, nonempty `prompt`, optional
   string `context`, a nonempty `expected_contains` string list, and an
   optional `expected_not_contains` list for canaries such as an injected
   instruction's payload. Explain
   which criteria cannot be verified by substring matching; put those in an
   anchored rubric for `/grade-rubric` instead of dropping them. Do not weaken criteria to fit the runner.
5. Keep reference answers out of candidate prompts. For workflow tasks, specify
   expected evidence and allowed tools separately; the runner does not execute
   tools or score traces.
6. Present proposed cases and rubrics for review before writing files. Keep
   held-out cases separate and do not tune prompts against them.

Return the task definition, cases, grading limitations, and a proposed offline
run command. Do not call live APIs or fabricate measured baseline results.
