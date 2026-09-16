"""One exact-base, grid-free wall chain reaches real CAD and survives restart."""
from __future__ import annotations
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.state_record import StateRecord
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings


class GridFreeWallIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="grid-free-wall-")
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name) / "free-wall"
        self.repository = FilesystemProjectRepository.initialize(self.project, project_id="free-wall",
            initial_state={"project_id":"free-wall", "version":0},
            authored_record=StateRecord(project_id="free-wall",run_id="authored",entities=()).to_dict())

    def entity(self, openings=()):
        return {"entity_id":"wall-free", "schema":"Element@1", "parent_id":"model", "fields":{
            "component_id":"model", "producer":"wall", "references":{
                "base":{"level":"ground"}, "line":{"from":{"point":[1,2]}, "to":{"point":["@wall_end",2]}}},
            "params":{"height":3, "thickness":.3, "openings":list(openings)}}}

    def request(self, digest):
        return {"stateDigest":digest, "keep":["entity:ground"], "semanticEdit":{
            "summary":"Make an explicitly requested free-position wall",
            "parameters":[{"key":"wall_length","value":4,"unit":"m"},
                          {"key":"wall_end","value":5,"unit":"m","expr":"wall_length + 1","inputs":["wall_length"]}],
            "entities":[self.entity()]}}

    def submit(self, client, body):
        response = client.post("/api/proposals",json=body)
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def initialize(self, client):
        response = client.post("/api/project/modeling",json={"projectId":"free-wall"})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(client.get("/api/state/frame").json()["axes"],[])
        compiler = Mock()
        compiler.compile.side_effect=AssertionError("direct semantic proposals cannot call a model")
        client.app.state.intent_compiler=compiler
        return client.get("/api/state").json()["stateDigest"],compiler

    def checkpoint(self, client, proposal):
        response=client.post(f"/api/proposals/{proposal['proposalId']}/candidate")
        self.assertEqual(response.status_code,202,response.text)
        accepted=response.json()
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            job=client.get(f"/api/jobs/{accepted['jobId']}").json()
            if job["status"] in ("succeeded","failed"):break
            time.sleep(.02)
        self.assertEqual(job["status"],"succeeded",job)
        return accepted["candidateId"],client.get(f"/api/candidates/{accepted['candidateId']}").json()

    def retained(self, run_id):
        refs=self.repository.list_json(run=self.repository.load_run(run_id),destination=PersistenceDestination(PersistenceArea.RUN_RECORD,run_id=run_id))
        records=[payload for ref in refs if (payload:=self.repository.load_json(ref)).get("schema")==StateRecord.SCHEMA]
        self.assertEqual(len(records),1)
        return StateRecord.from_dict(records[0])

    def test_refused_inputs_are_repaired_on_the_same_chain_without_execution(self):
        with TestClient(create_app(StudioSettings(cad_export="off",project_dir=self.project))) as client:
            digest,compiler=self.initialize(client)
            request=self.request(digest)
            invalid=copy.deepcopy(request)
            invalid["semanticEdit"]["entities"][0]["fields"]["references"]["line"]["from"]={"grid":"nonexistent"}
            response=client.post("/api/proposals",json=invalid)
            self.assertEqual(response.status_code,422,response.text)
            self.assertEqual(response.json()["code"],"SEMANTIC_EDIT_INVALID")
            first=self.submit(client,request)
            # The rejected edit may not replace the last valid proposal or relax keeps.
            bad={"stateDigest":digest,"sourceProposalId":first["proposalId"],"semanticEdit":{
                "summary":"An opening too wide for this wall","entities":[self.entity([{
                    "opening_id":"window","kind":"window","along":2,"width":50,"sill":1,"head":2}])]}}
            response=client.post("/api/proposals",json=bad)
            self.assertEqual(response.status_code,422,response.text)
            self.assertEqual(response.json()["code"],"SEMANTIC_EDIT_INVALID")
            good=copy.deepcopy(bad)
            good["semanticEdit"]["entities"][0]["fields"]["params"]["openings"][0]["width"]=1
            second=self.submit(client,good)
            final=self.submit(client,{"stateDigest":digest,"sourceProposalId":second["proposalId"],"semanticEdit":{
                "summary":"Extend the same wall, retaining its opening","parameters":[{"key":"wall_length","value":6}]}})
            self.assertEqual(final["baseStateDigest"],digest)
            self.assertIn("entity:ground",final["protected"])
            self.assertEqual(list((self.project/"runs").iterdir()),[])
            before=client.get(f"/api/proposals/{final['proposalId']}").json()
            stale=client.post("/api/proposals",json={**good,"stateDigest":"0"*64})
            self.assertEqual(stale.status_code,409,stale.text)
            self.assertEqual(stale.json()["code"],"STALE_BASE")
            self.assertEqual(client.get(f"/api/proposals/{final['proposalId']}").json(),before)
            run,_=self.checkpoint(client,final)
            self.assertEqual(len(list((self.project/"runs").iterdir())),1)
            record=self.retained(run)
            self.assertFalse(any(e.schema=="GridAxis@1" for e in record.entities))
            wall=next(e for e in record.entities if e.entity_id=="wall-free")
            self.assertEqual(wall.fields["producer"],"wall")
            self.assertEqual(wall.fields["params"]["openings"][0]["opening_id"],"window")
            self.assertEqual(wall.fields["references"]["line"]["to"]["point"],["@wall_end",2])
            values={p.key:p.value for p in record.parameters}
            self.assertEqual((values["wall_length"],values["wall_end"]),(6,7))
            compiler.compile.assert_not_called()

    @unittest.skipUnless(importlib.util.find_spec("OCP") and importlib.util.find_spec("rhino3dm"),"OCCT and preview dependencies are required")
    def test_real_export_cut_and_parametric_continuation_survive_restart(self):
        from archflow.adapters.occt_backend import read_step, measure_shape, classify_program_point
        settings=StudioSettings(cad_export="occt",project_dir=self.project)
        with TestClient(create_app(settings)) as client:
            digest,_=self.initialize(client)
            head_before=(self.project/"HEAD").read_bytes()
            authored_before=(self.project/"input/runner/state-record.json").read_bytes()
            request=self.request(digest)
            request["semanticEdit"]["entities"]=[self.entity([{"opening_id":"window","kind":"window","along":2,"width":1,"sill":1,"head":2}])]
            first=self.submit(client,request)
            final=self.submit(client,{"stateDigest":digest,"sourceProposalId":first["proposalId"],"semanticEdit":{
                "summary":"Extend before the single checkpoint","parameters":[{"key":"wall_length","value":6}]}})
            run,candidate=self.checkpoint(client,final)
            self.assertTrue(candidate["seatExecutionComplete"],candidate)
            self.assertIsNone(candidate["objectReadbackError"],candidate)
            self.assertTrue(candidate["objects"],candidate)
            names={o["name"] for o in candidate["objects"]}
            self.assertTrue(all(o["componentId"]=="model" for o in candidate["objects"]))
            self.assertEqual(len(list((self.project/"runs").iterdir())),1)
            steps=list((self.project/"runs"/run).rglob("*.step"))
            self.assertTrue(steps,"the candidate must retain real STEP geometry")
            # STEP also retains the hidden aperture as inspection evidence.
            # Check the cut wall and that positive void separately, never sum
            # the inspection solid back into the wall's material volume.
            entries={entry.name:entry for entry in read_step(steps[0],length_unit="meter")}
            self.assertEqual(set(entries),{"obj-wall-free-cut","obj-wall-free-aperture-window"})
            cut=entries["obj-wall-free-cut"].shape
            aperture=entries["obj-wall-free-aperture-window"].shape
            for shape in (cut,aperture):
                measured=measure_shape(shape)
                self.assertTrue(measured.valid and measured.closed)
                self.assertIsNotNone(measured.volume)
            self.assertAlmostEqual(measure_shape(cut).volume,(6*3-1)*.3,places=6)
            self.assertAlmostEqual(measure_shape(aperture).volume,.3,places=6)
            for depth in (1.72,1.85,1.98):
                self.assertEqual(classify_program_point(cut,(3,1.5,depth)),"outside")
                self.assertEqual(classify_program_point(aperture,(3,1.5,depth)),"inside")
                self.assertEqual(classify_program_point(cut,(1.5,1.5,depth)),"inside")
        with TestClient(create_app(settings)) as reopened:
            state=reopened.get(f"/api/state?run={run}").json()
            self.assertEqual(next(e for e in state["elements"] if e["elementId"]=="wall-free")["producer"],"wall")
            proposal=self.submit(reopened,{"stateDigest":state["stateDigest"],"sourceRunId":run,"keep":["entity:ground"],"semanticEdit":{
                "summary":"Continue after reopening","parameters":[{"key":"wall_length","value":7}]}})
            next_run,updated=self.checkpoint(reopened,proposal)
            self.assertIsNone(updated["objectReadbackError"],updated)
            self.assertEqual({o["name"] for o in updated["objects"]},names)
            continued_steps=list((self.project/"runs"/next_run).rglob("*.step"))
            self.assertTrue(continued_steps)
            continued={entry.name:entry for entry in read_step(continued_steps[0],length_unit="meter")}
            self.assertAlmostEqual(measure_shape(continued["obj-wall-free-cut"].shape).volume,(7*3-1)*.3,places=6)
            self.assertEqual(classify_program_point(continued["obj-wall-free-cut"].shape,(3,1.5,1.85)),"outside")
            self.assertEqual(len([e for e in self.retained(next_run).entities if e.entity_id=="wall-free"]),1)
            self.assertEqual((self.project/"HEAD").read_bytes(),head_before)
            self.assertEqual((self.project/"input/runner/state-record.json").read_bytes(),authored_before)
