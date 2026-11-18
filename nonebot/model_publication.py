"""Explicit publication of complete model configuration snapshots.

A publisher must supply the expected SHA-256 of a separately prepared file.
Unpublished or subsequently edited configurations are rejected by readers.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import tempfile
from pathlib import Path

import yaml


class UnpublishedConfig(RuntimeError):
    pass


def marker_path(target):
    target = Path(target)
    return target.with_name(target.name + '.published-sha256')


def read_published(target):
    target = Path(target)
    marker = marker_path(target)
    try:
        before = marker.read_text(encoding='ascii').strip()
        content = target.read_bytes()
        after = marker.read_text(encoding='ascii').strip()
    except (OSError, UnicodeError) as exc:
        raise UnpublishedConfig('model configuration is not published') from exc
    if (not re.fullmatch(r'[0-9a-f]{64}', before) or before != after
            or hashlib.sha256(content).hexdigest() != before):
        raise UnpublishedConfig('model configuration changed without publication')
    return content


def _atomic_write(path, content):
    fd, temporary = tempfile.mkstemp(prefix='.model-publish-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _validate_content(content, expected_sha256):
    if not re.fullmatch(r'[0-9a-f]{64}', expected_sha256):
        raise ValueError('expected SHA-256 is invalid')
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ValueError('prepared file does not match expected SHA-256')
    try:
        configs = yaml.safe_load(content)
        if not isinstance(configs, dict) or not configs or not all(
                isinstance(name, str) and name and isinstance(value, dict)
                for name, value in configs.items()):
            raise ValueError('invalid model configuration')
    except (ValueError, yaml.YAMLError):
        raise ValueError('invalid model configuration') from None
    return content


def approve_existing(target, expected_sha256):
    """Explicitly approve reviewed bytes without rewriting the existing file."""
    target = Path(target)
    content = target.read_bytes()
    _validate_content(content, expected_sha256)
    _atomic_write(marker_path(target), (expected_sha256 + '\n').encode('ascii'))
    if read_published(target) != content:
        raise UnpublishedConfig('configuration changed during approval')


def publish_file(source, target, expected_sha256):
    source, target = Path(source), Path(target)
    marker = marker_path(target)
    if source.resolve() in (target.resolve(), marker.resolve()):
        raise ValueError('source must be a separate prepared file')
    for candidate in (target, marker):
        if candidate.exists() and os.path.samefile(source, candidate):
            raise ValueError('source must be a separate prepared file')
    content = source.read_bytes()
    _validate_content(content, expected_sha256)
    _atomic_write(target, content)
    _atomic_write(marker, (expected_sha256 + '\n').encode('ascii'))


def main():
    parser = argparse.ArgumentParser(description='Publish a separately prepared model configuration.')
    parser.add_argument('source', type=Path)
    parser.add_argument('sha256', help='SHA-256 of the complete prepared file')
    parser.add_argument('--target', type=Path, default=Path(__file__).resolve().parent / 'configs' / 'models.yml')
    parser.add_argument('--approve-existing', action='store_true', help='Approve a reviewed existing target without rewriting it')
    args = parser.parse_args()
    try:
        if args.approve_existing:
            if args.source.resolve() != args.target.resolve():
                raise ValueError('source must be the target when approving existing configuration')
            approve_existing(args.target, args.sha256.lower())
        else:
            publish_file(args.source, args.target, args.sha256.lower())
    except (OSError, ValueError, UnpublishedConfig):
        parser.exit(1, 'Publication failed; check the prepared file, digest, and target.\n')
    print('Model configuration published; reload it in MuiceBot before background requests.')


if __name__ == '__main__':
    main()
