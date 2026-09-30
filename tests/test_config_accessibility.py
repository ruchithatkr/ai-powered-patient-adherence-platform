import pytest

from app.settings import Settings
from guardrails.accessibility import check_accessibility, reading_grade
from workflows.templates import LocaleTemplate, Template, TemplateLibrary


def test_real_phi_mode_is_refused():
    with pytest.raises(ValueError):
        Settings(allow_real_phi=True)
    with pytest.raises(ValueError):
        Settings(synthetic_data_only=False)


def test_policy_and_templates_load_from_versioned_files(rt):
    assert rt.policy.policy_version and rt.policy.digest and rt.templates.version
    assert rt.policy.risk.status == "PLACEHOLDER_UNAPPROVED"


def test_template_with_unapproved_field_is_rejected():
    tpl = Template(
        id="X",
        version="1",
        kb=None,
        high_touch=False,
        standing_approval={"granted": False},
        locales={"en": LocaleTemplate(status="APPROVED", approval_record="r", body="Hi {first_name} {ssn}")},
    )
    with pytest.raises(ValueError, match="unapproved personalisation fields"):
        TemplateLibrary("v", {"X": tpl})


def test_draft_locale_is_not_usable(rt):
    tpl = rt.templates.get("REMINDER_SIMPLE")
    assert tpl.approved_locale("en") and tpl.approved_locale("es") is None
    tpl.locales["es"] = LocaleTemplate(status="DRAFT", approval_record="none", body="Hola {first_name}")
    assert tpl.approved_locale("es") is None


def test_first_name_is_sanitised():
    assert TemplateLibrary.safe_first_name("Al<script>ex") == "Alscriptex"
    assert TemplateLibrary.safe_first_name("123") == "there"


def test_all_approved_templates_are_accessible_and_safe(rt):
    from guardrails.checks import check_content

    ap = rt.policy.accessibility
    for tpl in rt.templates.templates.values():
        for lang, loc in tpl.locales.items():
            body = loc.body.format(
                first_name="Alex",
                medication_label="blood-pressure medicine (morning)",
                time="08:00",
                kb_text="Link your medicine to a daily habit, like brushing your teeth. This can help you remember.",
            )
            assert check_content(body).decision == "PASS", (tpl.id, lang)
            res = check_accessibility(body, ap.max_reading_grade, ap.max_sms_chars, "sms")
            assert res.decision == "PASS", (tpl.id, lang, res.detail, reading_grade(body))


def test_accessibility_check_flags_hard_long_and_shouting_text():
    hard = "Notwithstanding pharmacological considerations, adherence optimisation necessitates comprehensive multidisciplinary intervention."
    assert check_accessibility(hard, 8.0, 320, "sms").decision == "BLOCK"
    assert check_accessibility("Take care. " * 60, 8.0, 320, "sms").decision == "BLOCK"
    assert check_accessibility("Take care. " * 60, 8.0, 320, "email").decision == "PASS"
    assert check_accessibility("URGENT ACTION NEEDED now.", 8.0, 320, "sms").decision == "BLOCK"
    assert reading_grade("The cat sat on the mat.") < reading_grade(
        "Pharmacological considerations necessitate comprehensive intervention."
    )
