"""Formatting must preserve literals, token boundaries and preprocessor behavior."""
from pathlib import Path
import tempfile
import unittest

from tools.format_cpp import owned_files, verify_equivalent


class CppFormattingGuardTests(unittest.TestCase):
    def test_indentation_and_line_wrapping_are_allowed(self):
        verify_equivalent(
            b'namespace n{int f(int x){return x+1;}} //same comment\n',
            b'namespace n {\n    int f(int x) {\n        return x + 1;\n    }\n} // same comment\n',
        )

    def test_string_character_and_raw_string_contents_are_protected(self):
        pairs = [
            (b'auto s = "keep two  spaces";', b'auto s = "keep two spaces";'),
            (b"auto c = ' ';", b"auto c = '\\t';"),
            (b'auto s = R"tag(a  b\n\"c\")tag";', b'auto s = R"tag(a b\n\"c\")tag";'),
            (b'auto s = R"(a\\\nb)";', b'auto s = R"(ab)";'),
            (b'auto s = "a\\nb";', b'auto s = "a b";'),
        ]
        for before, after in pairs:
            with self.subTest(before=before), self.assertRaises(ValueError):
                verify_equivalent(before, after)

    def test_whitespace_cannot_merge_lexical_tokens(self):
        for before, after in [
            (b'int x = a + +b;', b'int x = a ++b;'),
            (b'unsigned long x;', b'unsignedlong x;'),
            (b'int x = 1 e+2;', b'int x = 1e+2;'),
            (b'int x; / * comment * /', b'int x; /* comment */'),
        ]:
            with self.subTest(before=before), self.assertRaises(ValueError):
                verify_equivalent(before, after)

    def test_directive_boundaries_macro_spacing_and_header_names_are_protected(self):
        for before, after in [
            (b'#define F(x) x\n', b'#define F (x) x\n'),
            (b'#define TEXT(x) #x\n', b'#define TEXT(x) # x\n'),
            (b'#if ENABLED\nint x;\n#endif\n', b'#if ENABLED int x;\n#endif\n'),
            (b'#include <some header.h>\n', b'#include <someheader.h>\n'),
            (b'#include <a.h>\n#include <b.h>\n', b'#include <b.h>\n#include <a.h>\n'),
        ]:
            with self.subTest(before=before), self.assertRaises(ValueError):
                verify_equivalent(before, after)

    def test_newlines_cannot_extend_a_line_comment_over_code(self):
        with self.assertRaises(ValueError):
            verify_equivalent(b'// comment\nint x;\n', b'// comment int x;\n')

    def test_line_sensitive_constructs_require_manual_review(self):
        for source in (b'int x = __LINE__;', b'#line 42\nint x;',
                       b'int na\\\nme;', b'// comment\\\nint x;'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                verify_equivalent(source, source)

    def test_vendor_and_unrelated_files_are_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = ['native/include/owned.hpp', 'native/src/owned.cpp',
                     'native/tests/owned.cpp', 'native/vendor/external.cpp',
                     'native/src/metadata.json', 'other/extra.cpp']
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('')
            self.assertEqual([path.relative_to(root).as_posix() for path in owned_files(root)],
                             names[:3])


if __name__ == '__main__':
    unittest.main()
