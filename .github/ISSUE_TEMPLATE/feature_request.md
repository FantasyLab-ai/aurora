---
name: Feature Request
about: Suggest a new feature or improvement
title: '[FEATURE] '
labels: enhancement
assignees: ''
---

## What you're trying to do

Describe the use case. What are you analysing? Why are you reaching for Aurora? What's getting in the way today?

## What you'd like Aurora to do

Concrete description of the feature. If you have a draft API / UI / contract shape in mind, paste it.

## Which surface?

- [ ] Web Studio (visual / interactive)
- [ ] Python SDK (`aurora_sdk`)
- [ ] MCP server (`aurora_mcp`)
- [ ] Decision Contracts
- [ ] Research Kit
- [ ] Knowledge bank entries / domain pack
- [ ] New analytical method
- [ ] Documentation / examples

## Alternatives you've considered

What workaround do you have today? What other tools have you tried? Why aren't they enough?

## Glass-box compatibility check

If your feature involves the LLM, the data, or the analytical pipeline, confirm:

- [ ] Compatible with local-first (no required cloud calls)
- [ ] Doesn't require the LLM to make analytical decisions
- [ ] Findings remain deterministic / reproducible
- [ ] Sampling / timeouts can be honestly disclosed

If your feature *can't* satisfy one of these — that's OK to file, but please call out the tradeoff explicitly. Aurora's principles are non-negotiable but exceptions are sometimes worth discussing.

## Priority hint

How blocked are you?

- [ ] Showstopper — can't use Aurora without this
- [ ] Major — significant friction; have a workaround that's painful
- [ ] Nice-to-have — would be useful when prioritised
