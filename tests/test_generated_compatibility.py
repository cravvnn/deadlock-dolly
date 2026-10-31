"""Reviewed data generation must preserve gates and refuse incomplete evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import generate_compatibility as generate


class GeneratedCompatibilityTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        shutil.copytree(ROOT / 'native/profiles', self.root / 'native/profiles')
        (self.root / 'dolly').mkdir()
        for name in ('preload','replay_camera','follow_capabilities','follow_target'):
            shutil.copy2(ROOT/'dolly'/f'{name}.py', self.root/'dolly'/f'{name}.py')
        self.unlocker = self.root / 'third_party/cvar_unlocker'
        (self.unlocker / 'bin/win64').mkdir(parents=True)
        for name in ('THIRD_PARTY.json', 'bin/win64/server.dll'):
            shutil.copy2(ROOT/'third_party/cvar_unlocker'/name, self.unlocker/name)
        self.path = self.root / generate.CONTRACT
        self.data = generate.read_json(self.path)

    def save(self):
        self.path.write_text(json.dumps(self.data),encoding='utf-8')

    def test_repository_generated_files_are_current_without_game_access(self):
        with patch.object(generate.camera,'Image',side_effect=AssertionError('No game binary needed')):
            generate.check_generated(ROOT)

    def test_preload_singleton_rva_must_match_its_reviewed_constructor_store(self):
        namespace = {}
        exec(generate.generated_outputs(self.root)['dolly/_runtime_generated.py'], namespace)
        self.assertEqual(namespace['Preload'].INTRO_GLOBAL, 0x3ec89b8)
        original = deepcopy(self.data['groups']['Preload']['INTRO_GLOBAL'])
        # The old copied address stays in range and leaves every saved code
        # hash unchanged. The instruction/target relation must catch it.
        cases = [dict(original, value='0x3e80bd8'),
                 dict(original, rip_reference=dict(rva=original['rip_reference']['rva'],
                                                   bytes='90'+original['rip_reference']['bytes'][2:])),
                 dict(original, value='0x3ec88b9',
                      rip_reference=dict(rva=hex(int(original['rip_reference']['rva'],16)+1),
                                         bytes=original['rip_reference']['bytes'])),
                 dict(original, value='0x38adbe5',
                      rip_reference=dict(rva='0x1600000', bytes=original['rip_reference']['bytes']))]
        for entry in cases:
            with self.subTest(entry=entry):
                self.data['groups']['Preload']['INTRO_GLOBAL'] = entry
                self.save()
                with self.assertRaises(generate.ContractError):
                    generate.generated_outputs(self.root)

    def test_installed_verification_checks_the_rip_instruction_bytes(self):
        _, modules, _ = generate.load_contract(self.root)
        def image(path):
            module = next(m for m in modules.values() if path.as_posix().endswith(m['path']))
            return SimpleNamespace(sha256=module['sha256'], image_size=module['image_size'],
                                   read=lambda _rva, size: b'\x90' * size, close=Mock())
        with patch.object(generate.camera, 'resolve_game_dir', return_value=self.root), \
                patch.object(generate.camera, 'Image', side_effect=image), \
                self.assertRaisesRegex(generate.ContractError, 'RIP reference instruction differs'):
            generate.verify_game(self.root, self.root)

    def test_runtime_byte_guard_rejects_stale_displacement_and_invalid_address(self):
        # This complete instruction prefix is the reviewed live01 failure:
        # only the final RIP displacement changed, so whole-body evidence alone
        # must not stand in for checking the separate runtime guard literal.
        actual = bytes.fromhex('488bc45553564154415541564157488d68a14881ecf0000000448b0520431e02')
        entries = {'Update': {'kind': 'rva', 'module': 'client', 'value': '0x1000'}}
        entry = {'kind': 'bytes', 'module': 'client', 'at': 'Update', 'value': actual.hex()}
        image = SimpleNamespace(read=Mock(return_value=actual))
        generate.verify_byte_reference(image, entry, entries, 0x2000)
        image.read.assert_called_once_with(0x1000, 32)
        stale = dict(entry, value=actual[:-4].hex()+'70331c02')
        with self.assertRaisesRegex(generate.ContractError, 'runtime guard bytes differ'):
            generate.verify_byte_reference(image, stale, entries, 0x2000)
        for invalid in (dict(entry, at='missing'), dict(entry, at_offset=-1),
                        dict(entry, module='tier0'), dict(entry, at_offset=4096)):
            with self.subTest(entry=invalid), self.assertRaises(generate.ContractError):
                generate.byte_reference(invalid, entries, 0x2000)

    def test_unlocker_metadata_emits_one_artifact_contract_for_both_languages(self):
        artifact = (self.unlocker/'bin/win64/server.dll').read_bytes()
        metadata = generate.read_json(self.unlocker/'THIRD_PARTY.json')
        outputs = generate.generated_outputs(self.root)
        namespace = {}
        exec(outputs['dolly/_runtime_generated.py'], namespace)
        contract = namespace['Unlocker']
        self.assertEqual(contract.SHA256, hashlib.sha256(artifact).hexdigest())
        self.assertEqual(contract.SIZE, len(artifact))
        self.assertEqual(contract.VERSION, metadata['version'])
        header = outputs['native/include/dolly_runtime_generated.hpp']
        section = re.search(r'namespace Unlocker \{(.*?)\}', header, re.S).group(1)
        self.assertIn(f'SHA256[] = "{contract.SHA256}";', section)
        self.assertIn(f'SIZE = {hex(len(artifact))};', section)
        self.assertIn(f'VERSION[] = "{metadata["version"]}";', section)

    def test_unlocker_metadata_refuses_other_paths_bad_identity_types_and_bounds(self):
        path = self.unlocker/'THIRD_PARTY.json'
        original = generate.read_json(path)
        cases = (('bundled_file', '../server.dll'), ('bundled_file', 'other.dll'),
                 ('bundled_file_sha256', 'G'*64), ('bundled_file_sha256', '0'*64),
                 ('bundled_file_size', True), ('bundled_file_size', 2*1024*1024+1),
                 ('bundled_file_size', original['bundled_file_size']-1),
                 ('version', 6805), ('version', 'v1\nforged'), ('version', 'v1'+'x'*80))
        for field, value in cases:
            metadata = {**original, field: value}
            path.write_text(json.dumps(metadata), encoding='utf-8')
            with self.subTest(field=field, value=value), self.assertRaises(generate.ContractError):
                generate.generated_outputs(self.root)
        for content in (b'{"version":"v1","version":"v2"}', b' '*(64*1024+1)):
            path.write_bytes(content)
            with self.subTest(content_bytes=len(content)), self.assertRaises(generate.ContractError):
                generate.generated_outputs(self.root)

    def test_changed_unlocker_artifact_refuses_before_the_real_builder_deletes_previous_outputs(self):
        import build_native
        artifact = self.unlocker/'bin/win64/server.dll'
        artifact.write_bytes(artifact.read_bytes()+b'changed')
        dll = self.root/'native/bin/win64/DollyNative.dll'
        dll.parent.mkdir(parents=True)
        dll.write_bytes(b'previous native DLL, never executable')
        metadata = self.root/'native/build_info.json'
        metadata.write_bytes(b'previous build metadata')
        before = (dll.read_bytes(), metadata.read_bytes())
        with patch.object(sys, 'platform', 'win32'), \
                patch.object(build_native.platform, 'machine', return_value='AMD64'), \
                patch.object(build_native.struct, 'calcsize', return_value=8), \
                patch('subprocess.run') as run:
            with self.assertRaisesRegex(generate.ContractError, 'artifact differs'):
                build_native.build_native(self.root)
        run.assert_not_called()
        self.assertEqual(before, (dll.read_bytes(), metadata.read_bytes()))
        self.assertFalse((self.root/'dolly/_runtime_generated.py').exists())

    def test_unlocker_metadata_change_requires_regeneration(self):
        outputs = generate.generated_outputs(self.root)
        for name, text in outputs.items():
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
        path = self.unlocker/'THIRD_PARTY.json'
        metadata = generate.read_json(path)
        metadata['version'] += '-reviewed'
        path.write_text(json.dumps(metadata), encoding='utf-8')
        with self.assertRaisesRegex(generate.ContractError, 'Generated compatibility drift'):
            generate.check_generated(self.root)

    def test_unlocker_contract_refuses_a_linked_artifact_ancestor(self):
        actual = self.unlocker/'retained-bin'
        original = self.unlocker/'bin'
        original.rename(actual)
        if sys.platform == 'win32':
            import _winapi
            _winapi.CreateJunction(str(actual), str(original))
        else:
            original.symlink_to(actual, target_is_directory=True)
        try:
            retained = (actual/'win64/server.dll').read_bytes()
            with self.assertRaisesRegex(generate.ContractError, 'ordinary files'):
                generate.generated_outputs(self.root)
            self.assertEqual(retained, (actual/'win64/server.dll').read_bytes())
        finally:
            if sys.platform == 'win32':
                original.rmdir()
            else:
                original.unlink()

    def test_saved_reviews_round_trip_and_keep_every_existing_camera_profile(self):
        before = (self.root/'native/profiles/manifest.json').read_bytes()
        outputs = generate.generated_outputs(self.root)
        for name,text in outputs.items():
            path = self.root/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(text,encoding='utf-8')
        generate.check_generated(self.root)
        manifest = json.loads(before)
        for digest in manifest['modules']['citadel/bin/win64/client.dll']['accepted']:
            self.assertIn(digest,outputs['native/src/dolly_compat_generated.hpp'])
        self.assertEqual(before,(self.root/'native/profiles/manifest.json').read_bytes())
        path = self.root/'dolly/_runtime_generated.py'
        path.write_text(path.read_text()+'# stale edit\n',encoding='utf-8')
        with self.assertRaisesRegex(generate.ContractError,'drift'):
            generate.check_generated(self.root)

    def test_unlisted_client_profile_cannot_silently_extend_native_support(self):
        before = generate.generated_outputs(self.root)
        (self.root/'native/profiles/unreviewed-client.json').write_text(json.dumps({
            'client':{'client_sha256':'f'*64,'main_view_setup_rva':'0x100'}}))
        self.assertEqual(before,generate.generated_outputs(self.root))

    def test_unknown_module_fingerprint_is_rejected_before_emission(self):
        p=self.root/'native/profiles'/self.data['modules']['client']['profile']
        profile=generate.read_json(p)
        profile['client']['client_sha256']='f'*64
        p.write_text(json.dumps(profile))
        with self.assertRaisesRegex(generate.ContractError,'already be reviewed'):
            generate.generated_outputs(self.root)

    def test_older_camera_support_does_not_widen_preload_support(self):
        self.data['modules']['client']['profile']='deadlock-2026-09-30-6726-complete.json'
        self.save()
        with self.assertRaisesRegex(generate.ContractError,'preload pin'):
            generate.generated_outputs(self.root)

    def test_named_field_cannot_drift_from_saved_getters(self):
        self.data['schema']['C_BasePlayerPawn.m_pObserverServices']['offset']='0xe40'
        self.save()
        with self.assertRaisesRegex(generate.ContractError,'saved stock getter'):
            generate.generated_outputs(self.root)

    def test_schema_proof_must_be_part_of_the_actual_runtime_code_audit(self):
        spans=self.data['groups']['FollowTarget']['TARGET_SPANS']['value']
        getter=self.data['schema']['C_BasePlayerPawn.m_pObserverServices']['getters'][0]['rva']
        next(row for row in spans if int(row['rva'],16)==int(getter,16))['sha256']='0'*64
        self.save()
        with self.assertRaisesRegex(generate.ContractError,'runtime code audit'):
            generate.generated_outputs(self.root)

    def test_missing_consumer_symbol_fails_before_any_output(self):
        del self.data['groups']['FollowTarget']['ENTITY_LIST']
        self.save()
        with self.assertRaisesRegex(generate.ContractError,'missing symbols'):
            generate.generated_outputs(self.root)
        self.assertFalse((self.root/'dolly/_runtime_generated.py').exists())

    def test_out_of_range_code_span_and_unknown_value_kind_are_rejected(self):
        original=deepcopy(self.data)
        for changed in ({'kind':'guessed','module':'client','value':'0x40'},
                        {'kind':'rva','module':'client','value':'0x80000000'},
                        {'kind':'offset','module':'client','value':True}):
            self.data=deepcopy(original)
            self.data['groups']['FollowTarget']['ENTITY_LIST']=changed
            self.save()
            with self.subTest(changed=changed), self.assertRaises(generate.ContractError):
                generate.generated_outputs(self.root)

    def test_wire_order_and_required_flags_are_preserved(self):
        self.data['attach_wire_fields'][:2]=list(reversed(self.data['attach_wire_fields'][:2]))
        self.save()
        with self.assertRaisesRegex(generate.ContractError,'ABI field order'):
            generate.generated_outputs(self.root)
        self.data=generate.read_json(ROOT/generate.CONTRACT)
        self.data['attach_fields'][0]['required']='yes'
        self.save()
        with self.assertRaisesRegex(generate.ContractError,'required/optional'):
            generate.generated_outputs(self.root)

    def test_retained_anchor_bytes_round_trip_with_current_review(self):
        outputs=generate.generated_outputs(self.root)
        header=outputs['native/src/dolly_follow_anchor_generated.hpp']
        arrays=re.findall(r'constexpr unsigned char kFollowCode(\d+)_([0-9a-f]+)\[\] = \{(.*?)\};',header,re.S)
        actual={(build,int(rva,16)):bytes(int(v,16) for v in re.findall(r'0x([0-9a-f]+)',body))
                for build,rva,body in arrays}
        expected={(a['build'],int(span['rva'],16)):bytes.fromhex(span['bytes'])
                  for a in self.data['follow_anchors'] for span in a['spans']}
        self.assertEqual(actual,expected)
        anchors={a['build']:a for a in self.data['follow_anchors']}
        self.assertEqual(set(anchors),{'6722','6723','6726','6793','6796','6801','6803','6805','6806','6807','6821','6826','6827','6828','6829','6831','6835'})
        for build in ('6722','6723','6726'):
            self.assertEqual(anchors[build]['values']['observer_services_offset'],'0xe40')
            self.assertNotIn('local_player_table',anchors[build]['values'])
        self.assertEqual(anchors['6793']['values']['observer_services_offset'],'0xe98')
        player_table=0x2748d20  # Dedicated current review; camera-only updates may retain it.
        self.assertEqual(int(anchors['6793']['values']['local_player_table'],16),player_table)
        self.assertEqual(anchors['6796']['values']['observer_services_offset'],'0xe98')
        self.assertEqual(int(anchors['6796']['values']['local_player_table'],16),player_table)
        self.assertEqual(anchors['6801']['values']['observer_services_offset'],'0xe98')
        self.assertEqual(int(anchors['6801']['values']['local_player_table'],16),0x276c3d8)
        self.assertEqual(anchors['6803']['values']['observer_services_offset'],'0xe98')
        self.assertEqual(int(anchors['6803']['values']['local_player_table'],16),0x276c3d8)
        self.assertEqual(anchors['6805']['values']['observer_services_offset'],'0xe98')
        self.assertEqual(int(anchors['6805']['values']['local_player_table'],16),0x276c3d8)
        self.assertEqual(anchors['6806']['values']['observer_services_offset'],'0xe98')
        self.assertEqual(int(anchors['6806']['values']['local_player_table'],16),0x276c3d8)
        self.assertEqual(anchors['6807']['values']['observer_services_offset'],'0xe98')
        self.assertEqual(int(anchors['6807']['values']['local_player_table'],16),0x276c3d8)
        self.assertEqual(anchors['6821']['values']['observer_services_offset'],'0xe98')
        self.assertEqual(int(anchors['6821']['values']['local_player_table'],16),0x276d3d8)
        self.assertEqual(anchors['6826']['values']['observer_services_offset'],'0xe98')
        self.assertEqual(int(anchors['6826']['values']['local_player_table'],16),0x279a180)
        self.assertEqual(anchors['6827']['values']['observer_services_offset'],'0xe98')
        self.assertEqual(int(anchors['6827']['values']['local_player_table'],16),0x279b310)
        self.assertEqual(anchors['6828']['values']['observer_services_offset'],'0xe98')
        self.assertEqual(int(anchors['6828']['values']['local_player_table'],16),0x279b310)
        self.assertEqual(anchors['6829']['values']['observer_services_offset'],'0xe98')
        self.assertEqual(int(anchors['6829']['values']['local_player_table'],16),0x279b310)
        self.assertEqual(anchors['6831']['values']['observer_services_offset'],'0xe98')
        self.assertEqual(int(anchors['6831']['values']['local_player_table'],16),0x279b310)
        self.assertEqual(anchors['6835']['values']['observer_services_offset'],'0xe98')
        self.assertEqual(int(anchors['6835']['values']['local_player_table'],16),0x279b2e0)
        loaded,_,_=generate.load_contract(self.root)
        current=next(a for a in loaded['follow_anchors'] if a['build']=='6796')
        self.assertEqual(current['_sha256'],'8dff04c139d20b78d8d2c7a3039b691cfc221b8460438ec6fc6107c315741a5c')
        legacy=next(a for a in loaded['follow_anchors'] if a['build']=='6793')
        self.assertEqual(legacy['_sha256'],'d61348c564ce931e969996af18dbdec8544643810f28f3d330fb94b33231c460')
        layout=re.search(r'struct FollowAnchorProfile \{(.*?)\};',header,re.S).group(1)
        fields=re.findall(r'std::uintptr_t (\w+);',layout)
        rows=re.findall(r'^\s*\{"[0-9a-f]{64}", (.*?), kFollowCode(\d+)Spans,',header,re.M)
        emitted={build:dict(zip(fields,(int(value.strip(),0) for value in row.split(','))))
                 for row,build in rows}
        self.assertEqual(set(emitted),set(anchors))
        for build in ('6722','6723','6726'):
            self.assertEqual(emitted[build]['local_player_table'],0)
        self.assertEqual(emitted['6793']['local_player_table'],player_table)
        self.assertEqual(emitted['6796']['local_player_table'],player_table)

    def test_follow_anchor_caller_must_end_a_saved_direct_call_to_blend(self):
        original=deepcopy(self.data)
        for invalid in ('outside_saved_spans','wrong_return_address','wrong_destination'):
            self.data=deepcopy(original)
            anchor=self.data['follow_anchors'][0]
            caller=int(anchor['values']['blend_caller'],16)
            if invalid=='outside_saved_spans':
                anchor['values']['blend_caller']='0x10'
            elif invalid=='wrong_return_address':
                anchor['values']['blend_caller']=hex(caller+1)
            else:
                span=next(span for span in anchor['spans']
                          if int(span['rva'],16)<=caller-5
                          and caller<=int(span['rva'],16)+len(bytes.fromhex(span['bytes'])))
                raw=bytearray.fromhex(span['bytes'])
                offset=caller-5-int(span['rva'],16)
                self.assertEqual(raw[offset],0xe8)
                raw[offset+1:offset+5]=struct.pack('<i',int(anchor['values']['blend'],16)+1-caller)
                span['bytes']=raw.hex()
            self.save()
            with self.subTest(invalid=invalid), self.assertRaisesRegex(generate.ContractError,'Follow blend caller'):
                generate.generated_outputs(self.root)

    def reviewed_anchor(self):
        return next(anchor for anchor in self.data['follow_anchors'] if anchor['build']=='6796')

    def test_current_follow_anchor_must_match_named_observer_services_field(self):
        original=deepcopy(self.data)
        for invalid in ('observer_field','local_player_type'):
            self.data=deepcopy(original)
            anchor=self.reviewed_anchor()
            module=self.data['modules']['client']
            profile=generate.read_json(self.root/'native/profiles'/module['profile'])[module['section']]
            anchor['profile']=module['profile']
            anchor['values']['image_size']=hex(profile[module['size_key']])
            anchor['values']['observer_services_offset']=self.data['schema'][anchor['schema_field']]['offset']
            anchor['values']['local_player_table']=self.data['groups']['FollowTarget']['PLAYER_PAWN_VTABLE']['value']
            if invalid=='observer_field':
                anchor['values']['observer_services_offset']='0xe40'
                error='Current Follow anchor observer field'
            else:
                anchor['values']['local_player_table']=self.data['groups']['FollowTarget']['OBSERVER_PAWN_VTABLE']['value']
                error='Current Follow local player type'
            self.save()
            with self.subTest(invalid=invalid), self.assertRaisesRegex(generate.ContractError,error):
                generate.generated_outputs(self.root)

    def test_follow_anchor_verification_requires_identity_before_code_reads(self):
        data,_,_=generate.load_contract(self.root)
        anchor=next(a for a in data['follow_anchors'] if a['build']=='6793')
        for invalid in ('fingerprint','image_size'):
            image=SimpleNamespace(
                sha256='0'*64 if invalid=='fingerprint' else anchor['_sha256'],
                image_size=int(anchor['values']['image_size'],16)+(0x1000 if invalid=='image_size' else 0),
                read=Mock(side_effect=AssertionError('Unreviewed module must not be read')))
            with self.subTest(invalid=invalid), self.assertRaisesRegex(generate.ContractError,'Follow anchor module differs'):
                generate.verify_follow_anchor(image,anchor)
            image.read.assert_not_called()

    def test_follow_anchor_verification_reads_every_exact_span_and_refuses_changes(self):
        data,_,_=generate.load_contract(self.root)
        anchor=next(a for a in data['follow_anchors'] if a['build']=='6793')
        spans={int(span['rva'],16):bytes.fromhex(span['bytes']) for span in anchor['spans']}
        image=SimpleNamespace(sha256=anchor['_sha256'],image_size=int(anchor['values']['image_size'],16),
                              read=Mock(side_effect=lambda rva,size:spans[rva][:size]))
        self.assertEqual(generate.verify_follow_anchor(image,anchor),{
            'build':'6793','code_spans':len(spans),'live_verified':False})
        self.assertEqual(image.read.call_args_list,[call(rva,len(raw)) for rva,raw in spans.items()])
        for changed_rva in spans:
            image.read.side_effect=lambda rva,size: bytes(size) if rva==changed_rva else spans[rva][:size]
            with self.subTest(rva=hex(changed_rva)), self.assertRaisesRegex(generate.ContractError,'Follow anchor code differs'):
                generate.verify_follow_anchor(image,anchor)

    def test_incomplete_or_duplicate_anchor_profiles_cannot_emit_hooks(self):
        original=deepcopy(self.data)
        self.data['follow_anchors'].append(deepcopy(self.data['follow_anchors'][0]))
        self.save()
        with self.assertRaisesRegex(generate.ContractError,'Duplicate'):
            generate.generated_outputs(self.root)
        self.data=original
        self.data['follow_anchors'][0]['spans']=[]
        self.save()
        with self.assertRaisesRegex(generate.ContractError,'exact code evidence'):
            generate.generated_outputs(self.root)

    def test_duplicate_json_keys_are_not_silently_overwritten(self):
        self.path.write_text('{"format":1,"format":2}')
        with self.assertRaisesRegex(generate.ContractError,'Duplicate JSON key'):
            generate.load_contract(self.root)

    def test_player_profiles_keep_exact_pairs_and_moved_producer(self):
        scenes = generate.player_scenes(self.root, self.data)
        self.assertEqual([row['rva'] for row in scenes], [0x564b0, 0x5c8e0, 0x5c8e0, 0x5c8f0, 0x5c8f0, 0x5c8f0, 0x5c8f0, 0x5c8f0, 0x5c8f0, 0x5c8f0, 0x5c9a0])
        self.assertEqual([row['arguments'] for row in scenes], [9, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10])
        self.assertEqual([row['fields'][1] for row in scenes],
                         [0x5d4fe8, 0x61e7d0, 0x61e860, 0x61e860, 0x61e860, 0x61e800, 0x61e800, 0x61e800, 0x61e800, 0x61e800, 0x61e810])
        header = generate.generated_outputs(self.root)['native/include/dolly_player_scene_generated.hpp']
        for scene in scenes:
            self.assertIn(scene['scene_hash'], header)
            self.assertIn(scene['renderer_hash'], header)

    def test_invalid_player_contracts_are_rejected_before_emission(self):
        path = self.root/'native/profiles'/self.data['player_scenes'][-1]['profile']
        original = generate.read_json(path)
        for edit in ('abi', 'prologue', 'stride', 'missing', 'unreviewed', 'rva', 'span'):
            profile = deepcopy(original)
            if edit == 'abi':
                profile['producer']['arguments'] = 9
            elif edit == 'prologue':
                profile['producer']['prologue'] = '00' * 32
            elif edit == 'stride':
                profile['layout']['record_stride'] = 64
            elif edit == 'missing':
                del profile['layout']['owner_offset']
            elif edit == 'unreviewed':
                profile['sha256'] = 'f' * 64
            elif edit == 'rva':
                profile['producer']['rva'] = '0x10000000'
            else:
                profile['producer']['end_rva'] = profile['producer']['rva']
            path.write_text(json.dumps(profile), encoding='utf-8')
            with self.subTest(edit=edit), self.assertRaises((generate.ContractError, KeyError)):
                generate.generated_outputs(self.root)
        path.write_text(json.dumps(original), encoding='utf-8')
        self.data['player_scenes'][-1]['renderer_sha256'] = 'f' * 64
        self.save()
        with self.assertRaisesRegex(generate.ContractError, 'renderer identity'):
            generate.generated_outputs(self.root)

    def test_duplicate_and_unlisted_player_profiles_cannot_enable_hooks(self):
        self.data['player_scenes'].append(deepcopy(self.data['player_scenes'][0]))
        self.save()
        with self.assertRaisesRegex(generate.ContractError, 'Duplicate'):
            generate.generated_outputs(self.root)
        self.data['player_scenes'].pop()
        self.data['player_scenes'][0]['profile'] = 'unlisted-scene.json'
        self.save()
        with self.assertRaisesRegex(generate.ContractError, 'explicitly listed'):
            generate.generated_outputs(self.root)

    def test_named_schema_verification_checks_name_offset_and_each_getter(self):
        name='C_BasePlayerPawn.m_pObserverServices'
        field=self.data['schema'][name]
        base=0x180000000
        record=struct.pack('<QQI',base+0x100,0,int(field['offset'],16))
        blocks={int(field['record_rva'],16):record,0x100:b'm_pObserverServices\0'}
        blocks.update({int(g['rva'],16):bytes.fromhex(g['bytes']) for g in field['getters']})
        image=SimpleNamespace(base=base,read=lambda rva,size:blocks[rva][:size])
        self.assertEqual(generate.verify_named_field(image,name,field),{'offset':0xe98,'getters':2})
        for address in list(blocks):
            old=blocks[address]
            blocks[address]=bytes(len(old))
            with self.subTest(address=address), self.assertRaises((generate.ContractError,KeyError)):
                generate.verify_named_field(image,name,field)
            blocks[address]=old


if __name__ == '__main__':
    unittest.main()
