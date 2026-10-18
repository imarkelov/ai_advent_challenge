import pytest

from android import AdbError, parse_uiautomator_xml, screen_markdown, ui_tree_to_flat

SAMPLE_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" clickable="false" bounds="[0,0][1080,1920]">
    <node index="0" text="Добавить адрес" resource-id="toolbar_title" class="android.widget.TextView" clickable="false" bounds="[40,100][700,180]"/>
    <node index="1" text="" resource-id="street_input" class="android.widget.EditText" clickable="true" content-desc="Улица" bounds="[40,300][1040,420]"/>
    <node index="2" text="Принять" resource-id="accept_button" class="android.widget.Button" clickable="true" bounds="[40,1600][1040,1700]"/>
  </node>
</hierarchy>"""


def test_parse_builds_tree():
    tree = parse_uiautomator_xml(SAMPLE_XML)
    assert tree["class"] == "android.widget.FrameLayout"
    assert len(tree["children"]) == 3


def test_flat_extracts_ids_and_text():
    flat = ui_tree_to_flat(parse_uiautomator_xml(SAMPLE_XML))
    ids = [node["resource_id"] for node in flat if node["resource_id"]]
    assert "accept_button" in ids
    assert any(node["text"] == "Принять" for node in flat)


def test_screen_markdown_lists_tags():
    md = screen_markdown(parse_uiautomator_xml(SAMPLE_XML), "AddNewAddress")
    assert md.startswith("# Экран AddNewAddress")
    assert "accept_button" in md
    assert "street_input" in md
    assert "Принять" in md


def test_invalid_xml_raises():
    with pytest.raises(AdbError):
        parse_uiautomator_xml("<not-xml")
