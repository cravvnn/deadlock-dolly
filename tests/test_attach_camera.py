"""Attach camera field contract: schema parsing, refusal and roster names."""
import contextlib
import io
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

from dolly import attach_camera

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import derive_attach_fields


SCENE_NODE = """
Class 'CGameSceneNode
          0          8        CGameSceneNode                           Unaccounted                              VTable?
          0          48       CGameSceneNode                           m_pOwner                                 CEntityInstance*                        (layout)
          0          64       CGameSceneNode                           m_pChild                                 CGameSceneNode*                         (layout)
          0          72       CGameSceneNode                           m_pNextSibling                           CGameSceneNode*                         (layout)
          0          200      CGameSceneNode                           m_vecAbsOrigin                           VectorWS
          0          212      CGameSceneNode                           m_angAbsRotation                         QAngle
          0          228      CGameSceneNode                           m_nParentAttachmentOrBone                int16
"""

BASE_MODEL_ENTITY = """
Class 'C_BaseModelEntity
          0          2184     C_BaseModelEntity                        m_vecViewOffset                          CNetworkViewOffsetVector                (layout)
+         2184       16       CNetworkViewOffsetVector                 m_vecX                                   CNetworkedQuantizedFloat
+         2184       24       CNetworkViewOffsetVector                 m_vecY                                   CNetworkedQuantizedFloat
+         2184       32       CNetworkViewOffsetVector                 m_vecZ                                   CNetworkedQuantizedFloat
"""

PLAYER_PAWN = """
Class 'C_CitadelPlayerPawn
          0          4536     C_CitadelPlayerPawn                      m_angEyeAngles                           QAngle
"""

ENTITY_LAYOUT = """
Class 'C_BaseEntity
          0          816      C_BaseEntity                             m_pGameSceneNode                         CGameSceneNode*                         (layout)
"""


def live_layouts():
    return {
        "CGameSceneNode": SCENE_NODE,
        "C_BaseModelEntity": BASE_MODEL_ENTITY,
        "C_CitadelPlayerPawn": PLAYER_PAWN,
        "C_BaseEntity": ENTITY_LAYOUT,
    }


def layouts():
    return {
        "CGameSceneNode": SCENE_NODE,
        "C_BaseModelEntity": BASE_MODEL_ENTITY,
        "C_CitadelPlayerPawn": PLAYER_PAWN,
    }


class FieldTests(unittest.TestCase):
    def test_required_fields_parse_with_verified_candidates(self):
        offsets = attach_camera.parse_fields(layouts())
        self.assertEqual(offsets["player_origin"], 200)
        self.assertEqual(offsets["player_angles"], 212)
        self.assertEqual(offsets["eye_offset"], 2184)
        self.assertEqual(offsets["eye_angles"], 4536)
        self.assertEqual(offsets["scene_child"], 64)
        self.assertEqual(offsets["scene_sibling"], 72)

    def test_inline_rows_are_ignored_for_the_base_field(self):
        offset, type_text = attach_camera.parse_field(BASE_MODEL_ENTITY, "m_vecViewOffset")
        self.assertEqual(offset, 2184)
        self.assertIn("CNetworkViewOffsetVector", type_text)

    def test_missing_required_field_refuses_the_contract(self):
        broken = layouts()
        broken["C_CitadelPlayerPawn"] = "Class 'C_CitadelPlayerPawn\n"
        with self.assertRaisesRegex(RuntimeError, "m_angEyeAngles"):
            attach_camera.parse_fields(broken)

    def test_retyped_field_refuses_the_contract(self):
        broken = layouts()
        broken["CGameSceneNode"] = SCENE_NODE.replace("VectorWS", "Vector")
        with self.assertRaisesRegex(RuntimeError, "m_vecAbsOrigin"):
            attach_camera.parse_fields(broken)

    def test_optional_fields_drop_out_without_refusal(self):
        offsets = attach_camera.parse_fields(layouts())
        self.assertNotIn("world_transform", offsets)
        self.assertNotIn("owner_entity", offsets)
        offsets = attach_camera.parse_fields({
            **layouts(),
            "C_BaseEntity": ("          0          1308     C_BaseEntity  "
                             "m_hOwnerEntity  CHandle< C_BaseEntity >  (layout)\n"),
        })
        self.assertEqual(offsets["owner_entity"], 1308)

    def test_out_of_range_offset_refuses(self):
        broken = layouts()
        broken["CGameSceneNode"] = SCENE_NODE.replace("200      CGameSceneNode",
                                                      "4        CGameSceneNode")
        with self.assertRaisesRegex(RuntimeError, "m_vecAbsOrigin"):
            attach_camera.parse_fields(broken)

    def test_field_report_marks_each_row(self):
        report = attach_camera.field_report(layouts())
        by_field = {entry["field"]: entry for entry in report}
        self.assertEqual(by_field["m_vecAbsOrigin"]["status"], "ok")
        self.assertEqual(by_field["m_vecAbsOrigin"]["offset"], 200)
        self.assertEqual(by_field["m_nodeToWorld"]["status"], "field")
        self.assertEqual(by_field["m_hOwnerEntity"]["status"], "layout")
        self.assertFalse(by_field["m_nodeToWorld"]["required"])


class ViewOffsetDecodeTests(unittest.TestCase):
    def test_strided_components_decode(self):
        raw = bytearray(40)
        struct.pack_into("<f", raw, 16, 0.0)
        struct.pack_into("<f", raw, 24, -2.5)
        struct.pack_into("<f", raw, 32, 85.0)
        self.assertEqual(attach_camera.decode_view_offset(bytes(raw)), (0.0, -2.5, 85.0))

    def test_short_buffer_is_refused(self):
        self.assertIsNone(attach_camera.decode_view_offset(b"\0" * 16))

    def test_implausible_components_are_refused(self):
        for value in (float("nan"), float("inf"), 500.0):
            with self.subTest(value=value):
                raw = bytearray(40)
                struct.pack_into("<f", raw, 32, value)
                self.assertIsNone(attach_camera.decode_view_offset(bytes(raw)))


class HeroNameTests(unittest.TestCase):
    def test_verified_stems_map_to_hero_names(self):
        cases = {
            "models/heroes_staging/astro/astro.vmdl": "Holliday",
            "models/heroes_wip/abrams/abrams.vmdl": "Abrams",
            "models/heroes_wip/familiar/familiar_wip.vmdl": "Rem",
            "models/heroes_staging/digger/digger.vmdl": "Mo and Krill",
            "models/heroes_staging/hornet_v3/hornet.vmdl": "Vindicta",
        }
        for model, expected in cases.items():
            with self.subTest(model=model):
                self.assertEqual(attach_camera.hero_name(model), expected)

    def test_unknown_stem_is_readable_but_not_guessed(self):
        self.assertEqual(attach_camera.hero_name("models/heroes_wip/viscous/viscous.vmdl"),
                         "Viscous")
        self.assertEqual(attach_camera.hero_name(""), "Unknown")


class QueryFieldOffsetTests(unittest.TestCase):
    class Controller:
        def __init__(self, layouts):
            self.layouts = layouts
            self.calls = []

        def _request(self, command, timeout=5):
            self.calls.append(command)
            return self.layouts[command.rsplit(" ", 1)[-1]]

    def test_query_returns_the_runtime_order(self):
        controller = self.Controller(live_layouts())
        offsets = attach_camera.query_field_offsets(controller)
        self.assertEqual(tuple(offsets), attach_camera.RUNTIME_FIELD_ORDER)
        self.assertEqual(offsets["scene_node"], 816)
        self.assertEqual(offsets["owner"], 48)
        self.assertEqual(offsets["player_origin"], 200)
        self.assertEqual(offsets["eye_offset"], 2184)
        self.assertEqual(offsets["eye_angles"], 4536)
        self.assertEqual(len(controller.calls), len(attach_camera.RUNTIME_LAYOUT_CLASSES))

    def test_query_refuses_a_missing_live_field(self):
        broken = live_layouts()
        broken["C_CitadelPlayerPawn"] = "Class 'C_CitadelPlayerPawn\n"
        with self.assertRaises(RuntimeError):
            attach_camera.query_field_offsets(self.Controller(broken))


class DeriveToolTests(unittest.TestCase):
    def test_tool_reports_ok_and_emits_json(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, text in (("CGameSceneNode", SCENE_NODE),
                               ("C_BaseModelEntity", BASE_MODEL_ENTITY),
                               ("C_CitadelPlayerPawn", PLAYER_PAWN)):
                (root / (name + ".txt")).write_text(text, encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = derive_attach_fields.main(["--directory", str(root), "--json"])
            self.assertEqual(code, 0)
            report = json.loads(output.getvalue())
            self.assertTrue(any(entry["field"] == "m_angEyeAngles" for entry in report))

    def test_tool_fails_when_a_required_field_is_absent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "CGameSceneNode.txt").write_text(SCENE_NODE, encoding="utf-8")
            output, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(stderr):
                code = derive_attach_fields.main(["--directory", str(root)])
            self.assertEqual(code, 1)
            self.assertIn("m_angEyeAngles", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
