"""Dataset-specific MCP tools for the public ChemX collection."""

from typing import Any

from chemx_integration import ChemXClient, ChemXIntegrationError

_CHEMX_CLIENT: ChemXClient | None = None


def _build_client() -> ChemXClient:
    global _CHEMX_CLIENT
    if _CHEMX_CLIENT is None:
        _CHEMX_CLIENT = ChemXClient()
    return _CHEMX_CLIENT


def _error_payload(error: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "error_type": type(error).__name__,
        "error": str(error),
    }


def _run_dataset_query(*, dataset_key: str, sql_query: str, row_limit: int) -> dict[str, Any]:
    try:
        payload = _build_client().execute_sql(dataset_key, sql_query, row_limit=row_limit)
        return {
            "ok": True,
            "rows": payload["rows"],
            "source": payload["source"],
            "returned_rows": payload["returned_rows"],
            "truncated": payload["truncated"],
        }
    except (ValueError, ChemXIntegrationError) as error:
        return _error_payload(error)


def tool_search_chemx_cytotox(*, sql_query: str, row_limit: int = 100) -> dict[str, Any]:
    """Search ChemX Cytotox with SQL.

    Table: `dataset`
    Dataset: nanoparticle cytotoxicity assays with particle descriptors, cell metadata, and viability readouts.
    Use exact column names shown below.
    Quote column names that contain spaces or symbols.
    SQL columns:
    `row_idx, sn, material, shape, coat_functional_group, synthesis_method, surface_charge, size_in_medium_nm, zeta_in_medium_mv, no_of_cells_cells_well, human_animal, cell_source, cell_tissue, cell_morphology, cell_age, time_hr, concentration, test, test_indicator, viability_%, doi, article_list, core_nm, hydrodynamic_nm, potential_mv, cell_type, journal_name, publisher, year, title, journal_is_oa, is_oa, oa_status, pdf, access`
    Sample row:
    `{"row_idx": 0, "material": "SiO2", "shape": "Sphere", "coat_functional_group": "CTAB", "surface_charge": "Negative", "size_in_medium_nm": 90.09, "cell_source": "Rat", "cell_type": "PC12", "time_hr": 12, "concentration": 1.95, "test": "MTT", "viability_%": 113.67, "doi": "10.3109/15376516.2015.1070229"}`
    Example:
    `SELECT material, cell_type, concentration, "viability_%" FROM dataset WHERE test = 'MTT' AND "viability_%" < 80 ORDER BY concentration DESC LIMIT 20`
    """
    return _run_dataset_query(
        dataset_key="cytotox",
        sql_query=sql_query,
        row_limit=row_limit,
    )


def tool_search_chemx_seltox(*, sql_query: str, row_limit: int = 100) -> dict[str, Any]:
    """Search ChemX SelTox with SQL.

    Table: `dataset`
    Dataset: nanoparticle antibacterial screening rows with MIC or zone-of-inhibition measurements.
    Use exact column names shown below.
    Quote column names that contain spaces or symbols.
    SQL columns:
    `row_idx, sn, np, coating, bacteria, mdr, strain, np_synthesis, method, mic_np_µg_ml, concentration, zoi_np_mm, np_size_min_nm, np_size_max_nm, np_size_avg_nm, shape, time_set_hours, zeta_potential_mV, solvent_for_extract, temperature_for_extract_C, duration_preparing_extract_min, precursor_of_np, concentration_of_precursor_mM, hydrodynamic_diameter_nm, ph_during_synthesis, reference, doi, article_list, journal_name, publisher, year, title, journal_is_oa, is_oa, oa_status, pdf, access`
    Sample row:
    `{"row_idx": 0, "np": "Ag", "bacteria": "Enterococcus faecalis", "np_synthesis": "green_synthesis", "method": "MIC", "mic_np_µg_ml": "32", "np_size_avg_nm": 20.0, "shape": "spherical", "solvent_for_extract": "water (deionized)", "precursor_of_np": "AgNO3", "doi": "10.22034/nmrj.2020.01.006", "year": 2020}`
    Example:
    `SELECT np, bacteria, method, "mic_np_µg_ml", np_size_avg_nm FROM dataset WHERE method = 'MIC' AND bacteria LIKE '%Staphylococcus%' ORDER BY np_size_avg_nm LIMIT 20`
    """
    return _run_dataset_query(
        dataset_key="seltox",
        sql_query=sql_query,
        row_limit=row_limit,
    )


def tool_search_chemx_synergy(*, sql_query: str, row_limit: int = 100) -> dict[str, Any]:
    """Search ChemX Synergy with SQL.

    Table: `dataset`
    Dataset: nanoparticle-drug combination records with MIC or ZOI measurements, FIC values, and synergy effects.
    Use exact column names shown below.
    Quote column names that contain spaces or symbols.
    SQL columns:
    `row_idx, sn, NP, bacteria, strain, NP_synthesis, drug, drug_dose_µg_disk, NP_concentration_µg_ml, NP_size_min_nm, NP_size_max_nm, NP_size_avg_nm, shape, method, ZOI_drug_mm_or_MIC _µg_ml, error_ZOI_drug_mm_or_MIC_µg_ml, ZOI_NP_mm_or_MIC_np_µg_ml, error_ZOI_NP_mm_or_MIC_np_µg_ml, ZOI_drug_NP_mm_or_MIC_drug_NP_µg_ml, error_ZOI_drug_NP_mm_or_MIC_drug_NP_µg_ml, fold_increase_in_antibacterial_activity, zeta_potential_mV, MDR, FIC, effect, reference, doi, article_list, time_hr, coating_with_antimicrobial_peptide_polymers, combined_MIC, peptide_MIC, viability_%, viability_error, journal_name, publisher, year, title, journal_is_oa, is_oa, oa_status, pdf, access`
    Sample row:
    `{"row_idx": 0, "NP": "Ag", "bacteria": "Escherichia coli", "strain": "MTCC 443", "NP_synthesis": "chemical_synthesis using sodium borohydride and silver ions", "method": "MIC", "ZOI_NP_mm_or_MIC_np_µg_ml": 40.0, "time_hr": 24.0, "doi": "10.1016/j.actbio.2007.11.006", "year": 2008}`
    Example:
    `SELECT NP, bacteria, drug, method, "ZOI_NP_mm_or_MIC_np_µg_ml", FIC, effect FROM dataset WHERE method = 'MIC' AND bacteria LIKE '%coli%' LIMIT 20`
    """
    return _run_dataset_query(
        dataset_key="synergy",
        sql_query=sql_query,
        row_limit=row_limit,
    )


def tool_search_chemx_nanozymes(*, sql_query: str, row_limit: int = 100) -> dict[str, Any]:
    """Search ChemX Nanozymes with SQL.

    Table: `dataset`
    Dataset: nanozyme catalytic kinetics with substrate ranges, pH, temperature, and reaction annotations.
    Use exact column names shown below.
    Quote column names that contain spaces or symbols.
    SQL columns:
    `row_idx, formula, activity, syngony, length, width, depth, surface, km_value, km_unit, vmax_value, vmax_unit, target_source, reaction_type, c_min, c_max, c_const, c_const_unit, ccat_value, ccat_unit, ph, temperature, doi, pdf, access, title, journal, year`
    Sample row:
    `{"row_idx": 0, "formula": "MnO2", "activity": "oxidase", "km_value": 0.03, "km_unit": "mM", "vmax_value": 0.046, "reaction_type": "ABTS", "c_min": 0.005, "c_max": 1.0, "ccat_value": 0.01, "ccat_unit": "mcg/ml", "ph": 3.8, "temperature": 25.0, "doi": "10.1080/03067319.2019.1599875", "year": 2019}`
    Example:
    `SELECT formula, activity, reaction_type, km_value, vmax_value, ph FROM dataset WHERE activity = 'oxidase' ORDER BY km_value LIMIT 20`
    """
    return _run_dataset_query(
        dataset_key="nanozymes",
        sql_query=sql_query,
        row_limit=row_limit,
    )


def tool_search_chemx_benzimidazoles(*, sql_query: str, row_limit: int = 100) -> dict[str, Any]:
    """Search ChemX Benzimidazoles with SQL.

    Table: `dataset`
    Dataset: benzimidazole antibacterial compounds with SMILES, bacteria labels, and extracted potency values.
    Use exact column names shown below.
    Quote column names that contain spaces or symbols.
    SQL columns:
    `row_idx, smiles, doi, title, publisher, year, access, compound_id, target_type, target_relation, target_value, target_units, bacteria, bacteria_unified, page_bacteria, origin_bacteria, section_bacteria, subsection_bacteria, page_target, origin_target, section_target, subsection_target, page_scaffold, origin_scaffold, page_residue, origin_residue, pdf`
    Sample row:
    `{"row_idx": 0, "smiles": "CCOC(=O)C1=C(Cn2cnc3ccccc32)NC(=O)NC1c1ccco1", "compound_id": "5a", "target_type": "MIC", "target_relation": "=", "target_value": "64", "target_units": "ug/mL", "bacteria": "E. c. 25922", "bacteria_unified": "Escherichia coli", "doi": "10.1002/cjoc.202200326", "year": 2022}`
    Example:
    `SELECT compound_id, bacteria_unified, target_type, target_value, target_units FROM dataset WHERE bacteria_unified = 'Escherichia coli' ORDER BY year DESC LIMIT 20`
    """
    return _run_dataset_query(
        dataset_key="benzimidazoles",
        sql_query=sql_query,
        row_limit=row_limit,
    )


def tool_search_chemx_oxazolidinones(*, sql_query: str, row_limit: int = 100) -> dict[str, Any]:
    """Search ChemX Oxazolidinones with SQL.

    Table: `dataset`
    Dataset: oxazolidinone antibacterial compounds with potency values and bacteria annotations extracted from papers.
    Use exact column names shown below.
    Quote column names that contain spaces or symbols.
    SQL columns:
    `row_idx, pdf, smiles, doi, title, publisher, year, access, compound_id, target_type, target_relation, target_value, target_units, bacteria, bacteria_name_unified, bacteria_info, page_bacteria, origin_bacteria, section_bacteria, subsection_bacteria, page_target, origin_target, section_target, subsection_target, column_prop, line_prop, page_scaffold, origin_scaffold, section_scaffold, subsection_scaffold, page_residue, origin_residue, section_residue`
    Sample row:
    `{"row_idx": 0, "smiles": "CC(=O)NC[C@H]1CN(c2ccc(-n3cccc3)c(F)c2)C(=O)O1", "compound_id": "1", "target_type": "pMIC", "target_value": "5,52", "target_units": "mol/l", "bacteria": "methicillin-susceptible S. aureus", "bacteria_name_unified": "Staphylococcus aureus", "bacteria_info": "Methicillin susceptible", "doi": "10.1080/08927020601188528", "year": 2007}`
    Example:
    `SELECT compound_id, bacteria_name_unified, target_type, target_value, target_units FROM dataset WHERE bacteria_name_unified = 'Staphylococcus aureus' LIMIT 20`
    """
    return _run_dataset_query(
        dataset_key="oxazolidinones",
        sql_query=sql_query,
        row_limit=row_limit,
    )


def tool_search_chemx_complexes(*, sql_query: str, row_limit: int = 100) -> dict[str, Any]:
    """Search ChemX Complexes with SQL.

    Table: `dataset`
    Dataset: metal complex records with extracted SMILES, metal identity, and target values.
    Use exact column names shown below.
    Quote column names that contain spaces or symbols.
    SQL columns:
    `row_idx, pdf, doi, doi_sourse, supplementary, title, publisher, year, access, compound_id, compound_name, SMILES, SMILES_type, metal, target, page_smiles, origin_smiles, page_metal, origin_metal, page_target_value, origin_target_value`
    Sample row:
    `{"row_idx": 0, "compound_id": "L1", "SMILES": "O=C(O)CN(CC(=O)O)Cc1cc([N+](=O)[O-])ccc1O", "SMILES_type": "ligand", "metal": "Ga", "target": "19,1", "doi": "10.1021/cr900325h", "year": 2010}`
    Example:
    `SELECT compound_id, metal, SMILES_type, target FROM dataset WHERE metal = 'Ga' LIMIT 20`
    """
    return _run_dataset_query(
        dataset_key="complexes",
        sql_query=sql_query,
        row_limit=row_limit,
    )


def tool_search_chemx_eyedrops(*, sql_query: str, row_limit: int = 100) -> dict[str, Any]:
    """Search ChemX EyeDrops with SQL.

    Table: `dataset`
    Dataset: ophthalmology permeability candidates with SMILES, names, logP, and permeability metadata.
    Use exact column names shown below.
    Quote column names that contain spaces or symbols.
    SQL columns:
    `row_idx, smiles, name, perm (cm/s), logP, doi, PMID, title, publisher, year, access, page, origin`
    Sample row:
    `{"row_idx": 0, "smiles": "NS(=O)(=O)c1ccc(Cl)cc1", "name": "4-Chlorobenzensulfonamide", "perm (cm/s)": null, "logP": "-4,26", "PMID": 14609285.0, "title": "Prediction of corneal permeability using artificial neural networks", "year": 2003, "origin": "table 3"}`
    Example:
    `SELECT name, smiles, "logP", "perm (cm/s)", "PMID" FROM dataset WHERE year >= 2000 ORDER BY name LIMIT 20`
    """
    return _run_dataset_query(
        dataset_key="eyedrops",
        sql_query=sql_query,
        row_limit=row_limit,
    )


def tool_search_chemx_co_crystals(*, sql_query: str, row_limit: int = 100) -> dict[str, Any]:
    """Search ChemX Co-crystals with SQL.

    Table: `dataset`
    Dataset: drug-coformer co-crystal records with component names, SMILES, stoichiometry, and photostability changes.
    Use exact column names shown below.
    Quote column names that contain spaces or symbols.
    SQL columns:
    `row_idx, pdf, doi, supplementary, authors, title, journal, year, page, access, name_cocrystal, name_cocrystal_type_file, name_cocrystal_page, name_cocrystal_origin, ratio_cocrystal, ratio_cocrystal_page, ratio_cocrystal_page.1, ratio_cocrystal_origin, name_drug, name_drug_type_file, name_drug_origin, name_drug_page, SMILES_drug, SMILES_drug_type_file, SMILES_drug_origin, SMILES_drug_page, name_coformer, name_coformer_type file, name_coformer_origin, name_coformer_page, SMILES_coformer, SMILES_coformer_type file, SMILES_coformer_origin, SMILES_coformer_page, photostability_change, photostability_change_type_file, photostability_change_origin, photostability_change_page`
    Sample row:
    `{"row_idx": 0, "name_drug": "Acacetin", "SMILES_drug": "COc1ccc(-c2cc(=O)c3c(O)cc(O)cc3o2)cc1", "name_coformer": "4,4'-Bipyridine", "SMILES_coformer": "c1cc(-c2ccncc2)ccn1", "ratio_cocrystal": "2:1", "photostability_change": "does not change", "doi": "10.1016/j.molstruc.2024.139270", "year": 2024}`
    Example:
    `SELECT name_drug, name_coformer, ratio_cocrystal, photostability_change FROM dataset WHERE photostability_change IS NOT NULL LIMIT 20`
    """
    return _run_dataset_query(
        dataset_key="co_crystals",
        sql_query=sql_query,
        row_limit=row_limit,
    )


def tool_search_chemx_nanomag(*, sql_query: str, row_limit: int = 100) -> dict[str, Any]:
    """Search ChemX Nanomag with SQL.

    Table: `dataset`
    Dataset: magnetic nanoparticle characterization with SAR, MRI, and magnetometry values.
    Use exact column names shown below.
    Quote column names that contain spaces or symbols.
    SQL columns:
    `row_idx, name, np_shell_2, np_hydro_size, xrd_scherrer_size, zfc_h_meas, htherm_sar, mri_r1, mri_r2, emic_size, instrument, core_shell_formula, np_core, np_shell, space_group_core, space_group_shell, squid_h_max, fc_field_T, squid_temperature, squid_sat_mag, coercivity, squid_rem_mag, exchange_bias_shift_Oe, vertical_loop_shift_M_vsl_emu_g, hc_kOe, doi, pdf, supp, journal, publisher, year, title, access, verification required, verified_by, verification_date, has_mistake_in_matadata, comment, article_name_folder, supp_info_name_folder`
    Sample row:
    `{"row_idx": 0, "np_core": "Fe1", "htherm_sar": 250.0, "emic_size": 12.5, "squid_h_max": 20.0, "squid_sat_mag": "211.0", "coercivity": "190.0", "squid_rem_mag": "50.0", "doi": "10.1002/anie.201609477", "year": 2016}`
    Example:
    `SELECT np_core, htherm_sar, emic_size, squid_sat_mag, coercivity FROM dataset WHERE htherm_sar IS NOT NULL ORDER BY htherm_sar DESC LIMIT 20`
    """
    return _run_dataset_query(
        dataset_key="nanomag",
        sql_query=sql_query,
        row_limit=row_limit,
    )


TOOL_REGISTRY = {
    "search_chemx_cytotox": tool_search_chemx_cytotox,
    "search_chemx_seltox": tool_search_chemx_seltox,
    "search_chemx_synergy": tool_search_chemx_synergy,
    "search_chemx_nanozymes": tool_search_chemx_nanozymes,
    "search_chemx_benzimidazoles": tool_search_chemx_benzimidazoles,
    "search_chemx_oxazolidinones": tool_search_chemx_oxazolidinones,
    "search_chemx_complexes": tool_search_chemx_complexes,
    "search_chemx_eyedrops": tool_search_chemx_eyedrops,
    "search_chemx_co_crystals": tool_search_chemx_co_crystals,
    "search_chemx_nanomag": tool_search_chemx_nanomag,
}


__all__ = [
    "TOOL_REGISTRY",
    "tool_search_chemx_benzimidazoles",
    "tool_search_chemx_co_crystals",
    "tool_search_chemx_complexes",
    "tool_search_chemx_cytotox",
    "tool_search_chemx_eyedrops",
    "tool_search_chemx_nanomag",
    "tool_search_chemx_nanozymes",
    "tool_search_chemx_oxazolidinones",
    "tool_search_chemx_seltox",
    "tool_search_chemx_synergy",
]
