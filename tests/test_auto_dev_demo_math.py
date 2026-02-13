# -*- coding: utf-8 -*-
from __future__ import annotations

from examples.auto_dev_demo.demo_math import add


def test_add_basic() -> None:
    assert add(2, 3) == 5

