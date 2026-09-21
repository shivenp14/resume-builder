"""Disposable API workspace for browser tests; never imports the user's database."""
import os
import tempfile

import uvicorn

with tempfile.TemporaryDirectory(prefix="morrow-ui-") as workspace:
    os.environ["RESUME_WORKSPACE_ROOT"] = workspace
    from backend.app import main

    class TestProvider:
        def analyze(self, job_description):
            return main.analyze_text(job_description)

        def generate_proposal(self, context):
            return {"schema_version": "1.0", "selected_entries": [{"content_item_id": entry["content_item_id"], "bullet_ids": entry["bullet_ids"]} for entry in context["base_snapshot"]["entries"]],
                    "bullet_changes": [], "warnings": [], "rationale": "UI test"}

    main._provider = lambda: TestProvider()
    uvicorn.run(main.app, host="127.0.0.1", port=8011, log_level="warning")
