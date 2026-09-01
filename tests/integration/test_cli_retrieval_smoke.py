from __future__ import annotations

import json
import os
import unittest

from archflow.adapters.cli_retrieval import (
    CliProviderSpec,
    CliRetrievalAdapter,
)
from archflow.ports.retrieval import RetrievalQuery, RetrievalStatus
from archflow.project import ProjectVersionRef


class LiveCliRetrievalSmoke(unittest.TestCase):
    def test_explicit_opt_in_cli_provider(self) -> None:
        encoded_command = os.environ.get(
            "ARCHFLOW_RETRIEVAL_SMOKE_COMMAND_JSON"
        )
        if not encoded_command:
            self.skipTest(
                "set ARCHFLOW_RETRIEVAL_SMOKE_COMMAND_JSON to an explicit "
                "JSON command array"
            )
        command = tuple(json.loads(encoded_command))
        adapter = CliRetrievalAdapter(
            CliProviderSpec(
                provider_id="provider.live-smoke",
                version=os.environ.get(
                    "ARCHFLOW_RETRIEVAL_SMOKE_VERSION",
                    "explicit-unknown",
                ),
                command=command,
                timeout_seconds=30,
            )
        )
        query = RetrievalQuery(
            query_id="query-live-001",
            project_id="live-smoke-project",
            run_id="run-live-001",
            base=ProjectVersionRef(
                "live-smoke-project",
                0,
                "2" * 64,
            ),
            query_text=(
                "Return bounded cited evidence relevant to a generic public "
                "building research request."
            ),
        )

        receipt = adapter.retrieve(query)

        self.assertIs(receipt.status, RetrievalStatus.SUCCESS)
        self.assertTrue(receipt.results)
        self.assertTrue(
            all(
                item.epistemic_status == "hypothesis"
                for item in receipt.results
            )
        )


if __name__ == "__main__":
    unittest.main()
