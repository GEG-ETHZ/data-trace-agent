---
description: Run promptfoo prompt security evaluation and summarise results
---

Run `make eval`. After it completes:

1. Report the overall pass rate (e.g. "9/10 tests passed — 90%")
2. List any failing tests by name with their actual output vs expected
3. If below the 90% threshold, suggest which prompt file to edit to fix the failure
4. If promptfoo is not installed, it will be fetched via npx automatically
