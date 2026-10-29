"""Panorama resources fail closed before a new owned native deployment."""
import binascii
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from dolly import panorama_assets as assets


def write_vpk(index, entries, *, version=2, preload=0, external=False):
    """Create small independent inline/archive fixtures, including preload bytes."""
    pairs = list(entries.items()) if isinstance(entries, dict) else list(entries)
    groups = {}
    for name, payload in pairs:
        path = Path(name)
        groups.setdefault((path.suffix[1:], path.parent.as_posix()), []).append((path.stem, payload))
    tree = bytearray()
    data = bytearray()
    for extension in sorted({key[0] for key in groups}):
        tree += extension.encode() + b'\0'
        for (ext, directory), members in sorted(groups.items()):
            if ext != extension:
                continue
            tree += directory.encode() + b'\0'
            for name, payload in members:
                head = payload[:preload]
                tail = payload[preload:]
                tree += name.encode() + b'\0'
                tree += struct.pack('<IHHIIH', binascii.crc32(payload) & 0xffffffff,
                                    len(head), 0 if external else 0x7fff, len(data), len(tail), 0xffff)
                tree += head
                data += tail
            tree += b'\0'
        tree += b'\0'
    tree += b'\0'
    header = struct.pack('<III', 0x55aa1234, version, len(tree))
    if version == 2:
        header += struct.pack('<IIII', 0 if external else len(data), 0, 0, 0)
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_bytes(header + tree + (b'' if external else data))
    if external:
        index.with_name(index.name[:-8] + '_000.vpk').write_bytes(data)


def css_properties(body):
    return {name.strip(): value.strip() for name, value in
            (item.split(':', 1) for item in body.split(';') if ':' in item)}


def css_rules(text):
    """``({selector: {property: value}}, {keyframes: {percent: {property: value}}})`` of a flat stylesheet."""
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    keyframes = {}
    def collect(match):
        keyframes[match.group(1)] = {float(percent): css_properties(body) for percent, body
                                     in re.findall(r'([\d.]+)%\s*\{([^{}]*)\}', match.group(2))}
        return ''
    text = re.sub(r"@keyframes\s+'([^']+)'\s*\{((?:[^{}]*\{[^{}]*\})*)\s*\}", collect, text)
    rules = {}
    for selector, body in re.findall(r'([^{}]+)\{([^{}]*)\}', text):
        rules.setdefault(' '.join(selector.split()), {}).update(css_properties(body))
    return rules, keyframes


class PanoramaAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.index = self.root / 'pak01_dir.vpk'
        self.stock = {name: ('stock fixture ' + name).encode() for name in assets.STOCK_UI_SHA256}
        self.pins = {name: hashlib.sha256(payload).hexdigest() for name, payload in self.stock.items()}

    def test_named_stock_resources_read_inline_external_and_preload(self):
        for version in (1, 2):
            for external in (False, True):
                with self.subTest(version=version, external=external):
                    write_vpk(self.index, self.stock, version=version, external=external, preload=5)
                    self.assertEqual(assets.read_vpk_entries(self.index, self.stock), self.stock)
                    with patch.object(assets, 'STOCK_UI_SHA256', self.pins):
                        assets.verify_stock_ui_assets(self.root)

    def test_changed_missing_and_crc_damaged_stock_are_rejected(self):
        cases = [dict(self.stock), dict(list(self.stock.items())[1:])]
        cases[0][next(iter(self.stock))] += b' game update'
        for payloads in cases:
            write_vpk(self.index, payloads)
            with self.subTest(names=list(payloads)), patch.object(assets, 'STOCK_UI_SHA256', self.pins):
                with self.assertRaisesRegex(ValueError, 'shader loading UI could not be verified'):
                    assets.verify_stock_ui_assets(self.root)
        write_vpk(self.index, self.stock)
        data = bytearray(self.index.read_bytes()); data[-1] ^= 1; self.index.write_bytes(data)
        with patch.object(assets, 'STOCK_UI_SHA256', self.pins), self.assertRaisesRegex(ValueError, 'CRC'):
            assets.verify_stock_ui_assets(self.root)

    def test_truncation_limits_duplicate_and_missing_archives_fail_closed(self):
        for blob in (b'', b'not a VPK index', struct.pack('<III', 0x55aa1234, 2, 0x7fffffff)):
            self.index.write_bytes(blob)
            with self.subTest(blob=blob), self.assertRaises(ValueError):
                assets.read_vpk_entries(self.index, self.stock)
        write_vpk(self.index, self.stock)
        with self.assertRaisesRegex(ValueError, 'oversized'):
            assets.read_vpk_entries(self.index, self.stock, max_payload_bytes=1)
        name, payload = next(iter(self.stock.items()))
        write_vpk(self.index, [(name, payload), (name, payload)])
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            assets.read_vpk_entry(self.index, name)
        write_vpk(self.index, self.stock, external=True)
        self.index.with_name('pak01_000.vpk').unlink()
        with patch.object(assets, 'STOCK_UI_SHA256', self.pins), self.assertRaisesRegex(ValueError, 'shader loading UI'):
            assets.verify_stock_ui_assets(self.root)

    def test_shipped_pack_is_exact_accepted_five_resource_card_and_capture_clean_payloads(self):
        from dolly.launcher import UI_OVERRIDE_PACK, UI_OVERRIDE_PACK_SHA256
        self.assertEqual(UI_OVERRIDE_PACK_SHA256, '1f6619324ebccebe0a88c47e1b7754703ed090e3ddff57a68873bc49eddccfc5')
        self.assertEqual(hashlib.sha256(UI_OVERRIDE_PACK.read_bytes()).hexdigest(), UI_OVERRIDE_PACK_SHA256)
        payloads = assets.read_vpk_entries(UI_OVERRIDE_PACK, assets.OVERRIDE_RESOURCES)
        self.assertEqual(len(payloads), 5)
        for name, expected in {
            'panorama/styles/citadel_client_status.vcss_c': 'e13218f995880a59727d3cd3dd043f236fd67959c7962d3ade3429c56a6017c4',
            'panorama/styles/citadel_hud_and_db_overlay.vcss_c': '13c7907c48109f40dd5a7bb23895b60f6d7b0308d0508659451cd96cdae28d41',
        }.items():
            self.assertEqual(hashlib.sha256(payloads[name]).hexdigest(), expected)
        source = Path(__file__).resolve().parents[1] / 'native/ui_override'
        layout = ET.parse(source / 'panorama/layout/hud_hideout.xml').getroot()
        main = layout.find("CitadelHudHideout/Panel[@class='MainContents']")
        host = main.find("Panel[@id='DollyShaderNoticeAnchorV2']")
        self.assertEqual(host.attrib['hittestchildren'], 'false')
        self.assertIn('16%', host.find("CitadelPrewarmAssetsStatus[@id='PrewarmStatus']").attrib['style'])
        self.assertIsNone(main.find("Panel[@class='BottomRight']/CitadelPrewarmAssetsStatus"))

    def test_build_tool_requires_and_compiles_both_layouts_and_all_three_styles(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('build_ui_override_test', root / 'tools/build_ui_override.py')
        tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)
        self.assertEqual(tool.EXPECTED_RESOURCES, assets.OVERRIDE_RESOURCES)
        sdk = self.root / 'sdk'
        (sdk / 'game/citadel').mkdir(parents=True)
        (sdk / 'game/citadel/gameinfo.gi').write_text('fixture')
        calls = []
        def compile_one(command, **kwargs):
            target = Path(command[command.index('-i') + 1])
            relative = target.relative_to(sdk / 'content/citadel_addons' / tool.ADDON)
            produced = (sdk / 'game/citadel_addons' / tool.ADDON / relative).with_suffix(tool.EXPECTED_SOURCES[relative.as_posix()])
            produced.parent.mkdir(parents=True, exist_ok=True)
            produced.write_bytes(b'ClientServerDebugStats GameLogoIcon DollyLoadingProbeV2 DollyFamiliarBoil DollyChalk '
                                 + relative.as_posix().encode())
            calls.append(target.suffix)
            return SimpleNamespace(returncode=0)
        with patch.object(tool.subprocess, 'run', side_effect=compile_one):
            output, compiled = tool.compile_sources(sdk, self.root / 'resourcecompiler.exe')
        self.assertEqual(sorted(calls), ['.css', '.css', '.css', '.xml', '.xml'])
        destination = self.root / 'pak02_dir.vpk'
        tool.pack(output, compiled, destination)
        self.assertEqual(set(assets.read_vpk_entries(destination, assets.OVERRIDE_RESOURCES)), assets.OVERRIDE_RESOURCES)
        with self.assertRaisesRegex(SystemExit, 'exactly the five'):
            tool.pack(output, [p for p in compiled if p.suffix != '.vxml_c'], destination)
        for path in compiled:   # the card before Kara's redesign: no Familiar boil, no chalk doodle
            path.write_bytes(b'ClientServerDebugStats GameLogoIcon DollyLoadingProbeV2 ' + path.name.encode())
        with self.assertRaisesRegex(SystemExit, 'DollyFamiliarBoil'):
            tool.pack(output, compiled, destination)
        source = self.root / 'incomplete_sources'; source.mkdir()
        with patch.object(tool, 'SOURCE', source), self.assertRaisesRegex(SystemExit, 'two layouts'):
            tool.compile_sources(sdk, self.root / 'resourcecompiler.exe')

    def test_source_export_includes_all_reviewed_ui_sources(self):
        root = Path(__file__).resolve().parents[1]
        listed = set((root / 'SOURCE_FILES.txt').read_text(encoding='utf-8').splitlines())
        sources = {path.relative_to(root).as_posix() for path in (root / 'native/ui_override').rglob('*')
                   if path.is_file() and path.suffix in ('.css', '.xml')}
        self.assertEqual(len(sources), 5)
        self.assertLessEqual(sources, listed)

    def test_controller_clears_original_native_publisher_bit_and_cached_refresh(self):
        from dolly.controller import Controller
        from dolly.lite_mode import REPLAY_INFO, REPLAY_INFO_OFFSET
        from dolly.native_bridge import NativeBridge, MAPPING_BYTES
        from dolly.path import Project
        class Memory(bytearray):
            def close(self):
                pass
        memory = Memory(MAPPING_BYTES)
        bridge = NativeBridge.create(mapping_factory=lambda name, size: memory,
                                     start_heartbeat=False, editor_pid=1001, token='a'*32)
        self.addCleanup(bridge.close)
        controller = Controller(); controller._session = SimpleNamespace(native=bridge)
        bridge.configure_shader_loading(True)
        previous = REPLAY_INFO.unpack_from(memory, REPLAY_INFO_OFFSET)[1]
        for stage in ('waiting_preload_intro', 'preloading', 'preload_ready', 'editing_ready'):
            controller._message(stage, startup_stage=stage)
            packet = REPLAY_INFO.unpack_from(memory, REPLAY_INFO_OFFSET)
            self.assertEqual(packet[6] & 4, 0)
            self.assertEqual(packet[1] & 1, 0)
            self.assertGreater(packet[1], previous)
            self.assertEqual(controller._state['startup_stage'], stage)
            previous = packet[1]
            bridge.configure_lite_replay(Project(), 1, total_ticks=120)
            self.assertEqual(REPLAY_INFO.unpack_from(memory, REPLAY_INFO_OFFSET)[6] & 4, 0)
            previous = REPLAY_INFO.unpack_from(memory, REPLAY_INFO_OFFSET)[1]


CARD = 'CitadelPrewarmAssetsStatus.StatusNotification.DollyLoadingProbeV2'
FAMILIAR_ICON = 'panorama/images/hud/abilities/familiar/familiar_helpers_psd.vtex'
CHALK = 'panorama/images/hideout/chalk_doodle07_psd.vtex'
# Figma [design reference] variants as Panorama (dx px, dy px, rotateZ deg): Figma's positive
# angle turns counter-clockwise, rotateZ clockwise. The prototype plays them in this
# order, one per 800 ms.
BOIL_POSES = {0: (0.0, 0.0, 0.0), 1: (0.5, 0.0, -0.4), 2: (0.0, -0.5, 0.5),
              3: (-0.5, 0.5, -0.3), 4: (0.5, 0.5, 0.35), 5: (-0.5, -0.5, -0.5)}
BOIL_ORDER = (0, 3, 1, 5, 2, 4)
# The pack compiled from the card before this redesign (500 px, teal top border). It only
# recognises that pack: re-pinning the rebuilt pack leaves this value alone.
OLD_CARD_PACK_SHA256 = '0af30912a471efc984e653ebc57a4c05f210e07f707f42d3bec997fc945b2fe1'


class LoadingCardDesignTests(unittest.TestCase):
    """Kara's Panorama "Loading shaders" card (Figma [design reference]) with the boiling Familiar icon."""

    def setUp(self):
        self.source = Path(__file__).resolve().parents[1] / 'native/ui_override/panorama'
        self.css = (self.source / 'styles/citadel_prewarm_assets_status.css').read_text(encoding='utf-8')
        self.rules, self.keyframes = css_rules(self.css)

    def rule(self, part=''):
        return self.rules[(CARD + ' ' + part).strip()]

    def test_shipped_pack_carries_this_card_once_rebuilt(self):
        from dolly.launcher import UI_OVERRIDE_PACK
        if hashlib.sha256(UI_OVERRIDE_PACK.read_bytes()).hexdigest() == OLD_CARD_PACK_SHA256:
            self.skipTest('native/assets/ui/pak02_dir.vpk still holds the card before this redesign; '
                          'rebuild it as native/ui_override/panorama/README.md says')
        # Compiled stylesheets keep their selectors, keyframe names and image URLs as text.
        card = assets.read_vpk_entry(UI_OVERRIDE_PACK, 'panorama/styles/citadel_prewarm_assets_status.vcss_c')
        for part in ('DollyTitleRow', 'DollyFamiliarBoil', 'DollyChalk', FAMILIAR_ICON, CHALK):
            self.assertIn(part.encode('ascii'), card)

    def test_layout_is_brand_then_icon_and_title_then_desc_under_the_chalk(self):
        card = ET.parse(self.source / 'layout/citadel_prewarm_assets_status.xml').getroot().find('CitadelPrewarmAssetsStatus')
        self.assertEqual(card.attrib['class'].split(), ['StatusNotification', 'DollyLoadingProbeV2'])
        self.assertEqual(card.attrib['hittest'], 'false')
        self.assertEqual([child.attrib['class'] for child in card], ['Details', 'DollyChalkClip'])
        brand, row, desc = list(card.find("Panel[@class='Details']"))
        self.assertEqual((brand.tag, brand.attrib['class'], brand.attrib['text']), ('Label', 'DollyBrand', 'Deadlock Dolly'))
        self.assertEqual((desc.tag, desc.attrib['class'], desc.attrib['text']),
                         ('Label', 'Desc', 'Your replay will open automatically.'))
        self.assertEqual(row.attrib['class'], 'DollyTitleRow')
        icon_slot, title = list(row)
        self.assertEqual([panel.attrib['class'] for panel in icon_slot], ['Icon'])
        self.assertEqual((title.tag, title.attrib['class'], title.attrib['text']), ('Label', 'Title', 'Loading shaders'))
        self.assertEqual([panel.attrib['class'] for panel in card.find("Panel[@class='DollyChalkClip']")], ['DollyChalk'])
        # Nothing for the engine to address by id: only the root's classes changed hands, as before.
        self.assertFalse([node for node in card.iter() if 'id' in node.attrib])
        host = ET.parse(self.source / 'layout/hud_hideout.xml').getroot().find(".//CitadelPrewarmAssetsStatus[@id='PrewarmStatus']")
        style = css_properties(host.attrib['style'])
        self.assertEqual((style['width'], style['horizontal-align'], style['position']), ('420px', 'center', '0px 16% 0px'))

    def test_card_box_and_type_follow_the_figma_frame(self):
        card = self.rule()
        for name, value in {'width': '420px', 'height': '112px', 'padding': '0px', 'flow-children': 'none',
                            'background-color': '#0f1012', 'border': '1px solid #29292d', 'border-radius': '6px',
                            'box-shadow': '#00000073 0px 8px 24px 0px'}.items():
            self.assertEqual(card[name], value, name)
        self.assertEqual((self.rule('.Details')['padding'], self.rule('.Details')['opacity']), ('21px', '1'))
        brand, row, title, desc = (self.rule(part) for part in ('.DollyBrand', '.DollyTitleRow', '.Title', '.Desc'))
        self.assertEqual({name: brand[name] for name in ('font-family', 'font-size', 'font-weight', 'line-height', 'color')},
                         {'font-family': 'sans', 'font-size': '12px', 'font-weight': 'medium', 'line-height': '16px', 'color': '#a6a6a6'})
        self.assertEqual({name: title[name] for name in ('font-family', 'font-size', 'font-weight', 'line-height', 'color', 'margin')},
                         {'font-family': 'oracle', 'font-size': '21px', 'font-weight': 'semi-bold', 'line-height': '26px',
                          'color': '#ffffff', 'margin': '0px 0px 0px 5px'})
        self.assertEqual({name: desc[name] for name in ('font-family', 'font-size', 'font-weight', 'line-height', 'color')},
                         {'font-family': 'sans', 'font-size': '12px', 'font-weight': 'normal', 'line-height': '16px', 'color': '#a6a6a6'})
        for rule in (brand, title, desc):
            self.assertEqual(rule['text-transform'], 'none')   # the stock card shouts in uppercase
        # 21 inset (20 padding inside the 1 px stroke) + 16 + 6 + 26 + 6 + 16 + 21 = the 112 px card.
        px = lambda value: float(value.split()[0].removesuffix('px'))
        self.assertEqual(2 * px(self.rule('.Details')['padding']) + px(brand['height']) + px(row['margin'])
                         + px(row['height']) + px(desc['margin']) + px(desc['height']), px(card['height']))
        slot, icon = self.rule('.IconContainer'), self.rule('.IconContainer .Icon')
        self.assertEqual((slot['visibility'], slot['width'], slot['height'], icon['width'], icon['height']),
                         ('visible', '26px', '26px', '26px', '26px'))
        self.assertEqual(icon['background-image'], f'url("s2r://{FAMILIAR_ICON}")')
        self.assertEqual(icon['wash-color'], 'none')   # the stock slot washes icons offWhite

    def test_engine_prewarming_class_stays_the_only_visibility_gate(self):
        gates = {selector: rule['visibility'] for selector, rule in self.rules.items()
                 if 'visibility' in rule and ' ' not in selector}
        self.assertEqual(gates, {'CitadelPrewarmAssetsStatus.StatusNotification': 'collapse',
                                 'CitadelPrewarmAssetsStatus.StatusNotification.Prewarming': 'visible'})
        self.assertFalse([selector for selector, rule in self.rules.items()
                          if ' ' not in selector and ('opacity' in rule or 'display' in rule)])
        animated = [selector for selector, rule in self.rules.items() if 'animation-name' in rule]
        self.assertEqual(animated, [CARD + '.Prewarming .IconContainer .Icon'])

    def test_familiar_icon_boils_through_six_held_poses_every_800_ms(self):
        animation = self.rules[CARD + '.Prewarming .IconContainer .Icon']
        self.assertEqual(animation, {'animation-name': 'DollyFamiliarBoil', 'animation-duration': '4.8s',
                                     'animation-timing-function': 'linear', 'animation-iteration-count': 'infinite'})
        shape = re.compile(r'translate3d\( (-?[\d.]+)px, (-?[\d.]+)px, 0px \) rotateZ\( (-?[\d.]+)deg \)$')
        def pose(transform):
            match = shape.match(transform)
            self.assertIsNotNone(match, transform)   # one function list everywhere: Panorama tweens like with like
            return tuple(float(value) for value in match.groups())
        frames = sorted((percent, pose(props['transform'])) for percent, props in self.keyframes['DollyFamiliarBoil'].items())
        self.assertEqual((frames[0][0], frames[-1][0]), (0.0, 100.0))
        holds = []
        for percent, value in frames:
            if holds and holds[-1][2] == value:
                holds[-1][1] = percent
            else:
                holds.append([percent, percent, value])
        self.assertEqual([value for _start, _end, value in holds], [BOIL_POSES[index] for index in BOIL_ORDER])
        starts = [start for start, _end, _value in holds] + [100.0]
        for (start, end, _value), following in zip(holds, starts[1:]):
            self.assertAlmostEqual((following - start) / 100 * 4800, 800, delta=1)   # held 800 ms...
            self.assertLessEqual((following - end) / 100 * 4800, 0.5)              # ...then a snap, not a tween
        self.assertEqual(pose(self.rule('.IconContainer .Icon')['transform']), BOIL_POSES[0])
        self.assertEqual(self.rule('.IconContainer .Icon')['transform-origin'], '50% 50%')

    def test_chalk_doodle_is_the_designs_right_hand_crop(self):
        chalk = self.rule('.DollyChalk')
        self.assertEqual(self.rule('.DollyChalkClip')['overflow'], 'clip')
        self.assertEqual(chalk['background-image'], f'url("s2r://{CHALK}")')
        self.assertEqual((chalk['opacity'], chalk['transform'], chalk['border-radius']), ('0.04', 'rotateZ( -5.75deg )', '34px'))
        width, height = (float(value.removesuffix('px')) for value in (chalk['width'], chalk['height']))
        texture_width, texture_height = (float(value.removesuffix('px')) for value in chalk['background-size'].split())
        offset = float(chalk['background-position'].split()[0].removesuffix('px'))
        # Figma: 768 x 420 texture, CROP 0.3166 wide from 0.6822, into 124.47 x 215.
        self.assertAlmostEqual(texture_width / texture_height, 768 / 420, places=3)
        self.assertEqual(texture_height, height)
        self.assertAlmostEqual(width / texture_width, 0.3166, places=3)
        self.assertAlmostEqual(-offset / texture_width, 0.6822, places=3)

    def test_card_images_exist_in_the_installed_game_when_present(self):
        from dolly import launcher
        install = launcher.discover_game()
        index = install and install / 'game/citadel/pak01_dir.vpk'
        if not index or not index.is_file():
            self.skipTest('Deadlock is not installed; game image check skipped')
        self.assertEqual(set(re.findall(r's2r://(panorama/images/[^"]+)"', self.css)), {FAMILIAR_ICON, CHALK})
        entries = assets.list_vpk_entries(index, prefixes=('panorama/images/',), suffixes=('.vtex_c',))
        for image in (FAMILIAR_ICON, CHALK):
            self.assertIn(image + '_c', entries)
