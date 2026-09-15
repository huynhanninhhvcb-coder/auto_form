from __future__ import annotations

import unittest

from services.template_service import get_template, list_templates


class TemplateServiceTests(unittest.TestCase):
    def test_lists_all_five_templates(self):
        ids = {item["id"] for item in list_templates()}
        self.assertEqual(
            ids,
            {
                "tro_cap_huu_tri",
                "ho_tro_hoa_tang",
                "ho_tro_mai_tang",
                "ho_tro_nq40",
                "ho_tro_nq32",
            },
        )

    def test_deceased_benefit_templates_share_the_same_required_fields(self):
        hoa_tang = get_template("ho_tro_hoa_tang")
        mai_tang = get_template("ho_tro_mai_tang")
        expected = ("full_name", "date_of_birth", "citizen_id", "residence_address", "deceased_full_name", "deceased_death_date")
        self.assertEqual(hoa_tang.required_fields, expected)
        self.assertEqual(mai_tang.required_fields, expected)

    def test_new_template_word_files_exist_on_disk(self):
        self.assertTrue(get_template("ho_tro_hoa_tang").path.is_file())
        self.assertTrue(get_template("ho_tro_mai_tang").path.is_file())
        self.assertTrue(get_template("ho_tro_nq40").path.is_file())
        self.assertTrue(get_template("ho_tro_nq32").path.is_file())

    def test_nq_templates_require_identity_issue_and_support_group(self):
        expected = (
            "full_name",
            "date_of_birth",
            "citizen_id",
            "citizen_id_issue_date",
            "citizen_id_issue_place",
            "residence_address",
            "support_category",
        )
        self.assertEqual(get_template("ho_tro_nq40").required_fields, expected)
        self.assertEqual(get_template("ho_tro_nq32").required_fields, expected)

    def test_unknown_template_returns_none(self):
        self.assertIsNone(get_template("khong-ton-tai"))


if __name__ == "__main__":
    unittest.main()
