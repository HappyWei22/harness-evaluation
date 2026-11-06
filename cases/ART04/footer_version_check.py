"""ART04首轮页脚补充观察1.0.0：只读OOXML，不给产品分、不改原检查器。"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET

VERSION = '2026.11-R3A4'
CHECKER_VERSION = 'art04-footer-observation-1.0.0'
NS = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
STRICT = 'Internal management review | 2026-11 | ' + VERSION


def version_text_ok(text):
    # 文本分隔符提前声明；保留点号、连字符、字母和数字，避免截短派生版本。
    tokens = re.split(r'[\s|:：;；,，()（）\[\]【】]+', text)
    versions = [t for t in tokens if re.search(r'\d{4}\.\d{2}[-‐‑‒–—―]', t)]
    return bool(versions) and all(t == VERSION for t in versions)


def shape_texts(root, name, preserve_breaks=True):
    texts = []
    for shape in root.findall('.//p:sp', NS) + root.findall('.//p:graphicFrame', NS):
        nv = shape.find('p:nvSpPr/p:cNvPr', NS)
        if nv is None:
            nv = shape.find('p:nvGraphicFramePr/p:cNvPr', NS)
        if nv is None or nv.get('name') != name:
            continue
        # 连续文字片段相接；段落、显式换行必须保留，不能拼成原本不存在的版本。
        paragraphs = []
        for paragraph in shape.findall('.//a:p', NS):
            paragraphs.append(''.join((node.text or '') if node.tag == '{' + NS['a'] + '}t' else '\n'
                                      for node in paragraph.iter()
                                      if node.tag in {'{' + NS['a'] + '}t', '{' + NS['a'] + '}br'}))
        texts.append('\n'.join(paragraphs) if preserve_breaks else ''.join(n.text or '' for n in shape.findall('.//a:t', NS)))
    return texts


def inspect(path, expected_slides=112):
    result = {'checker_version': CHECKER_VERSION, 'path': str(path),
              'scope': '首轮每页名为footer的形状；不含其他版本位置、业务、保留性或实际取件',
              'automatic_dimension_score': None}
    if not path.is_file():
        return dict(result, status='unknown', passed=None, reason='缺实际成品，不以输入或其他轮次替代')
    result['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if len(names) != len(set(names)):
                raise ValueError('ZIP存在重复条目')
            if len(names) > 5000 or sum(i.file_size for i in z.infolist()) > 500_000_000:
                raise ValueError('包超出检查上限')
            if any(n.startswith('/') or '..' in PurePosixPath(n).parts for n in names):
                raise ValueError('包存在不安全路径')
            presentation = ET.fromstring(z.read('ppt/presentation.xml'))
            rels = ET.fromstring(z.read('ppt/_rels/presentation.xml.rels'))
            relmap = {r.get('Id'): r for r in rels}
            parts = []
            for sid in presentation.findall('p:sldIdLst/p:sldId', NS):
                relation = relmap[sid.get('{' + NS['r'] + '}id')]
                if relation.get('TargetMode') == 'External' or not relation.get('Type', '').endswith('/slide'):
                    raise ValueError('幻灯片关系不是内部slide关系')
                target = relation.get('Target', '')
                part = target.lstrip('/') if target.startswith('/') else posixpath.normpath('ppt/' + target)
                if not re.fullmatch(r'ppt/slides/slide\d+\.xml', part):
                    raise ValueError('无法识别的幻灯片目标')
                parts.append(part)
            all_parts = {n for n in names if re.fullmatch(r'ppt/slides/slide\d+\.xml', n)}
            layout_ok = len(parts) == expected_slides and len(set(parts)) == len(parts) and set(parts) == all_parts
            rows = []
            for index, part in enumerate(parts, 1):
                root = ET.fromstring(z.read(part))
                texts = shape_texts(root, 'footer')
                ok = len(texts) == 1 and version_text_ok(texts[0])
                # 原程序拼接a:t并使用第一个同名形状，按整句精确匹配；只复现此原子方法。
                raw_texts = shape_texts(root, 'footer', preserve_breaks=False)
                raw_text = raw_texts[0] if raw_texts else ''
                strict = bool(texts) and raw_text == STRICT
                rows.append({'slide': index, 'part': part, 'footer_count': len(texts), 'texts': texts,
                             'passed': ok, 'strict_sentence_passed': strict})
            passed = layout_ok and bool(rows) and all(r['passed'] for r in rows)
            result.update(status='pass' if passed else 'fail', passed=passed, expected_slides=expected_slides,
                          slide_scope_ok=layout_ok, referenced_slides=len(parts),
                          matched=sum(r['passed'] for r in rows),
                          strict_sentence_matched=sum(r['strict_sentence_passed'] for r in rows), slides=rows)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        result.update(status='unreadable', passed=None, reason=str(exc))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pptx', type=Path)
    args = parser.parse_args()
    print(json.dumps(inspect(args.pptx), ensure_ascii=False, indent=2))
