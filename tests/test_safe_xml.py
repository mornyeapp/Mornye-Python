"""XML from the network must not be able to declare entities."""

from xml.etree import ElementTree

import pytest

from mornye.core import safe_xml
from mornye.core.apple_ttml import parse_ttml

BOMB = (
    '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaaaaaaaa">'
    '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]><tt>&b;</tt>'
)


def test_a_plain_document_parses() -> None:
    assert safe_xml.fromstring("<a><b>x</b></a>").find("b").text == "x"
    assert safe_xml.fromstring(b"<a/>").tag == "a"


@pytest.mark.parametrize(
    "document",
    [BOMB, BOMB.encode(), BOMB.encode("utf-16"), "<!doctype x><x/>"],
)
def test_a_document_type_declaration_is_refused(document) -> None:
    with pytest.raises(ElementTree.ParseError):
        safe_xml.fromstring(document)


def test_ttml_with_a_dtd_yields_no_lyrics() -> None:
    assert not parse_ttml(BOMB).lines
