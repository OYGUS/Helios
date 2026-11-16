"""Codebase, Understand-Anything (.ua), and Markdown Wiki Analysis Engine.

1. Imports `.ua/knowledge-graph.json` or `.understand-anything/knowledge-graph.json`
   directly if present (the graph is converted, not re-analysed).
2. Otherwise `build_code_map()` walks the directory once (pruning dependency/build/VCS
   folders), reads every analysable file exactly once and fills a `SolarSystem` in one
   of two modes.
   (see `connectors.codebase.mapbuild` for both map modes; the parts below are
   documented in the module that implements them).

Module map (`connectors/codebase/`, lower modules never import higher ones; listed
bottom-up):

   * `constants`     — tunables, titles and tables (ignored dirs, code extensions, stdlib
                       names, ...), `LAST_SCAN_FAILURES`.
   * `fileinfo`      — layer classification (`_classify_file_layer`) and the per-file
                       records `_FileInfo` / `_Def` / `_Method`.
   * `lang_common`   — text clipping, comment / string masking, brace matching and the
                       pattern-matched definitions of other languages (`_LANG_PATTERNS`).
   * `purpose_text`  — per-file purpose evidence (header comments, user-facing strings,
                       secret filters) and the README intro parser (`readme_intro`).
   * `lang_python`   — Python `ast` analysis (definitions, imports, raw call scopes).
   * `lang_patterns` — JS/TS, PHP and HTML analysis, the per-extension dispatcher and
                       Markdown.
   * `grouping`      — module (planet) grouping of files (`_group_modules`).
   * `walk`          — the single directory walk (`_walk_project`).
   * `resolve`       — import / include / reference resolution and entry points.
   * `calls`         — call relations (`_resolve_calls`).
   * `libraries`     — external libraries, the library table (`TECH_FINGERPRINTS`) and
                       dependency manifests.
   * `content`       — text helpers, the `_Analysis` result and sub-component content.
   * `purpose`       — purpose evidence assembly, `purpose_line`, `merge_purpose`, ...
   * `identity`      — the identity line (`identity_line`, `stored_identity`, ...).
   * `mapbuild`      — core plan text, file-level / directory maps, `build_code_map`.
   * `project`       — folder keys, ignored folders, refresh / merge of re-scanned
                       systems, duplicate cleanup, `LAST_SCAN_STATS`.
   * `importer`      — `CodebaseImporter` (directory / .ua import, parent scans).

This module is a facade: it re-exports every name of those modules that callers used to
import from here (public and private), so `from connectors.codebase_importer import X`
keeps working. Tests that monkeypatch a tunable must patch the submodule that reads it
(for example `connectors.codebase.mapbuild.SATELLITE_CAP`).
"""

from __future__ import annotations

# Modules and typing/model names that were importable from this module before the split
# (kept so that `codebase_importer.<name>` keeps resolving for every caller).
import ast  # noqa: F401
import bisect  # noqa: F401
import copy  # noqa: F401
import json  # noqa: F401
import logging  # noqa: F401
import os  # noqa: F401
import posixpath  # noqa: F401
import re  # noqa: F401
import sys  # noqa: F401
import warnings  # noqa: F401
from collections import Counter, defaultdict  # noqa: F401
from dataclasses import dataclass, field  # noqa: F401
from pathlib import Path, PurePosixPath, PureWindowsPath  # noqa: F401
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Set, Tuple  # noqa: F401

from core import paths as app_paths  # noqa: F401
from core.models import LAYER_DEFINITIONS, SOURCE_CATEGORIES, GalaxyStore, PlanetNode, SolarSystem  # noqa: F401
from connectors.purpose_evidence import (  # noqa: F401  (re-exported for callers of this module)
    PURPOSE_STRONG_SCORE,
    SESSION_EVIDENCE_LABEL,
    EvidenceStrength,
    assess_purpose_evidence,
    clean_header_text,
    is_noise_comment_line,
    purpose_evidence_is_strong,
    session_line_of,
)

# The implementation, re-exported name by name (public and private helpers alike: tests and
# other modules import them from here). Each name is the very object defined in its
# submodule, so module-level state such as `LAST_SCAN_STATS` exists once.
from connectors.codebase.constants import (  # noqa: F401
    AGENT_SOURCES,
    CALL_ANALYSIS_MAX_CODE_FILES,
    CALL_ANALYSIS_MAX_FILE_BYTES,
    CALL_REF_CAP,
    CODE_EXTENSIONS,
    DATA_EXTENSIONS,
    DATA_FILE_LIST_CAP,
    DATA_FILE_MAX_BYTES,
    DATA_FILE_NAMES,
    DOCS_PLANET_TITLE,
    DOMINANT_DIR_SHARE,
    EXTERNAL_PLANET_TITLE,
    FILE_LEVEL_MAX_FILES,
    FLAT_ROOT_SHARE,
    HISTORY_PLANET_TITLE,
    IGNORED_DIRS,
    IGNORED_DIR_SUFFIXES,
    LAST_SCAN_FAILURES,
    MAX_DESCEND_DEPTH,
    MAX_PARSED_FILES,
    MAX_PARSE_BYTES,
    MAX_PLANETS,
    METHOD_SATELLITES_MAX_TOTAL,
    METHOD_SATELLITE_MAX_PUBLIC,
    MODULE_CODE_TITLE,
    OLLAMA_MARKER,
    OTHER_FILES_PLANET_TITLE,
    OTHER_PLANET_TITLE,
    ROOT_PLANET_TITLE,
    SATELLITE_CAP,
    SUMMARY_FILE_LIST_CAP,
    _CONFIG_NAMES,
    _DOTNET_PROJECT_SUFFIXES,
    _IGNORED_DIRS_LOWER,
    _JS_EXTS,
    _JS_EXT_SET,
    _JS_TS_SUBST,
    _LAYER_PRIORITY,
    _LOCK_FILE_NAMES,
    _NODE_BUILTINS,
    _NPM_NAME_RE,
    _STDLIB_FALLBACK,
    _STDLIB_MODULES,
)
from connectors.codebase.fileinfo import (  # noqa: F401
    Chain,
    _ARCH_MD_STEMS,
    _CAMEL_RE,
    _Def,
    _ENTRY_STEMS,
    _EXT_LAYER_BONUS,
    _FileInfo,
    _LAYER_DIR_WORDS,
    _LAYER_STEM_WORDS,
    _Method,
    _TEST_DIRS,
    _TEST_STEM_FIRST,
    _TEST_STEM_LAST,
    _classify_file_layer,
    _split_tokens,
)
from connectors.codebase.lang_common import (  # noqa: F401
    _BODY_START_RE,
    _BRACE_RE,
    _C_KEYWORDS,
    _EXT_TO_PATTERN_KEY,
    _JS_IMPORT_PATTERNS,
    _JS_TOKEN_RE,
    _LANG_PATTERNS,
    _Lines,
    _NON_NL_RE,
    _PAREN_RE,
    _PHP_OPEN_RE,
    _PHP_TOKEN_RE,
    _SCRIPT_BLOCK_RE,
    _analyze_with_patterns,
    _blank,
    _body_after_params,
    _body_from,
    _brace_pairs,
    _clean_comment_line,
    _clip,
    _direct_member_checker,
    _mask_both,
    _match_paren,
    _nest_defs,
    _php_code_regions,
    _scope_finder,
    _script_blocks,
    _split_names,
)
from connectors.codebase.purpose_text import (  # noqa: F401
    CAPABILITIES_LABEL,
    PURPOSE_CANDIDATE_CAP,
    PURPOSE_EVIDENCE_BUDGET,
    PURPOSE_HEADER_MAX,
    PURPOSE_MAX_CHARS,
    PURPOSE_NAMES_MAX,
    PURPOSE_SESSION_RESERVE,
    PURPOSE_STRING_CAP,
    PURPOSE_STRING_MAX,
    README_INTRO_CAP,
    README_TEXT_CAP,
    SESSION_REQUESTS_MAX,
    SESSION_REQUEST_CHARS,
    _COMMAND_RE,
    _DESC_HEADING_RE,
    _DIRECTIVE_CODE_RE,
    _HASH_NON_COMMENT,
    _HEADER_SCAN_LINES,
    _HTML_H1_RE,
    _HTML_META_DESC_RES,
    _HTML_TITLE_RE,
    _IMPERATIVE_WORDS,
    _INSTR_HEADING_RE,
    _JS_DOC_TITLE_RE,
    _JS_STR_CONST_RE,
    _LIST_LINE_RE,
    _PHP_DB_RES,
    _PHP_STR_CONST_RE,
    _PURPOSE_DIRS,
    _PURPOSE_EXTS,
    _PURPOSE_NAME_RE,
    _PURPOSE_STEMS,
    _PY_TITLE_CALLS,
    _README_ALT_NAMES,
    _README_BOILERPLATE,
    _SECRET_NAME_RE,
    _SECRET_VALUE_RES,
    _TEMPLATE_EXPR_RE,
    _add_bit,
    _clean_md_inline,
    _comment_blocks,
    _descriptive,
    _docstring_description,
    _first_sentences,
    _generic_header_comment,
    _is_instruction_paragraph,
    _is_natural_text,
    _is_purpose_candidate,
    _is_secret_name,
    _looks_secret,
    _php_db_usage,
    _py_static_str,
    _python_purpose_bits,
    _read_small_text,
    _readme_sections,
    _strip_markup,
    _text_purpose_bits,
    _unescape_js,
    _valid_description,
    readme_intro,
)
from connectors.codebase.lang_python import (  # noqa: F401
    _ENUM_BASES,
    _FUNC_TYPES,
    _PY_CHILD_FIELDS,
    _PY_SCALAR_FIELDS,
    _PY_SKIP_TYPES,
    _abbrev,
    _analyze_python_source,
    _base,
    _bind_params,
    _bind_targets,
    _call_chain,
    _child_fields,
    _collect_python_calls,
    _collect_scope,
    _comment_above,
    _decorator_name,
    _first_comment_block,
    _first_line,
    _fmt_arg,
    _fmt_args,
    _fmt_def,
    _is_main_test,
    _iter_statements,
    _note_local,
    _py_class_def,
    _safe_unparse,
    _set_def_doc,
)
from connectors.codebase.lang_patterns import (  # noqa: F401
    _HTML_REF_RES,
    _JS_ARROW_PROP_RE,
    _JS_CJS_EXPORTS_RE,
    _JS_CJS_EXPORT_NAME_RE,
    _JS_CLASS_RE,
    _JS_EXPORT_DEFAULT_NAME_RE,
    _JS_FUNC_DECL_RE,
    _JS_FUNC_VAR_RE,
    _JS_IMPORT_CLAUSE_RE,
    _JS_METHOD_RE,
    _JS_NOT_METHODS,
    _JS_REQUIRE_DESTRUCT_RE,
    _JS_REQUIRE_WHOLE_RE,
    _JS_TYPE_RE,
    _PHP_CALL_RE,
    _PHP_CLASS_RE,
    _PHP_DIRNAME_RE,
    _PHP_DOCROOT_RE,
    _PHP_FUNC_RE,
    _PHP_INCLUDE_RE,
    _PHP_NAMESPACE_RE,
    _PHP_NOT_CALLS,
    _PHP_USE_RE,
    _analyze_generic_source,
    _analyze_js,
    _analyze_markdown_file,
    _analyze_markdown_text,
    _analyze_php,
    _collect_html_refs,
    _js_package_name,
    _parse_import_clause,
    _php_include_target,
    _php_string_literal,
    _split_concat,
)
from connectors.codebase.grouping import (  # noqa: F401
    _Module,
    _compute_expanded_dirs,
    _group_modules,
    _layout_is_flat,
    _majority_layer,
    _module_key,
)
from connectors.codebase.walk import (  # noqa: F401
    _BACKUP_DIR_RE,
    _WalkExtras,
    _is_backup_dir,
    _is_data_file,
    _should_skip_dir,
    _walk_project,
)
from connectors.codebase.resolve import (  # noqa: F401
    MAX_ENTRY_POINTS,
    _AliasConfig,
    _JSONC_COMMENT_RE,
    _PREFERRED_PY_ENTRIES,
    _PathIndex,
    _detect_entries,
    _in_scope,
    _js_candidates,
    _load_jsonc,
    _lookup_js_target,
    _norm_rel,
    _py_lookup,
    _py_package_dirs,
    _py_roots,
    _read_alias_configs,
    _resolve_alias,
    _resolve_html_refs,
    _resolve_js_imports,
    _resolve_js_spec,
    _resolve_php_includes,
    _resolve_php_uses,
    _resolve_python_imports,
)
from connectors.codebase.calls import (  # noqa: F401
    Relation,
    _PyCallResolver,
    _resolve_calls,
)
from connectors.codebase.libraries import (  # noqa: F401
    TECH_FINGERPRINTS,
    _External,
    _IMPORT_ONLY_TECH,
    _Manifests,
    _PY_IMPORT_ALIASES,
    _REQ_NAME_RE,
    _REQ_SPEC_RE,
    _STDLIB_CAPABILITY_NAMES,
    _Tech,
    _collect_dependency_names,
    _collect_externals,
    _collect_manifests,
    _detect_project_technologies,
    _match_fingerprints,
    _norm_dep,
    _parse_requirement_names,
    _parse_requirement_specs,
    _pipfile_dependency_names,
    _pyproject_dependency_names,
    _toml_array_strings,
    _toml_load,
    _toml_section_keys,
    _tomllib,
)
from connectors.codebase.content import (  # noqa: F401
    _Analysis,
    _SatRefs,
    _about_line,
    _bullets,
    _capped,
    _def_content,
    _defined_names,
    _defines_line,
    _dep_label,
    _edge_label,
    _external_split,
    _file_content,
    _file_head,
    _how_defs_found,
    _import_label,
    _keep_only_real_links,
    _key_display,
    _layer_color,
    _layer_title,
    _list_block,
    _method_entry,
    _names_satellite_content,
    _normalized_path_key,
    _plural,
    _ref_lines,
    _sat_title,
    _short_file_label,
    _significance,
    _source_label,
    _uses_lines,
)
from connectors.codebase.purpose import (  # noqa: F401
    _ESSENTIAL_PREFIXES,
    _GENERIC_NAMES,
    _PHP_INCLUDE_STEMS,
    _PurposeInfo,
    _SHAPE_CAPABILITIES,
    _capability_groups,
    _collect_purpose,
    _evidence_capabilities,
    _format_capabilities,
    _header_of,
    _names_line,
    _purpose_file_order,
    _purpose_origin,
    _shape_line,
    apply_purpose_text,
    compose_purpose_evidence,
    format_session_requests,
    merge_purpose,
    purpose_line,
    update_core_plan_purpose,
)
from connectors.codebase.identity import (  # noqa: F401
    IDENTITY_DEV_TOOLS,
    IDENTITY_KINDS,
    IDENTITY_MAX_CHARS,
    IDENTITY_MAX_LIBS,
    IDENTITY_SEP,
    IDENTITY_TOOLTIP,
    MARKER_AIOHTTP_WEB,
    MARKER_CLI,
    MARKER_GODOT,
    MARKER_ROOT_INDEX_PHP,
    MARKER_UNITY,
    _IDENTITY_MARKUP_LANGS,
    _PAREN_NAMES_RE,
    _SHAPE_RE,
    _WEB_KINDS,
    _analysis_identity,
    _identity_library_order,
    _is_dev_tool,
    _signal_matches,
    identity_kind,
    identity_languages,
    identity_line,
    merge_identity,
    stored_identity,
)
from connectors.codebase.mapbuild import (  # noqa: F401
    CodeMapStats,
    _add_external_planet,
    _build_dir_level,
    _build_file_level,
    _core_plan_text,
    _file_planet_summary,
    _file_significance,
    _module_summary,
    _plan_file_satellites,
    build_code_map,
    count_code_files,
)
from connectors.codebase.project import (  # noqa: F401
    GENERATED_ONLY_PLANET_KINDS,
    IGNORED_FOLDERS_SETTING,
    LAST_SCAN_STATS,
    _STATS_PATH_CAP,
    _WIN_PATH_RE,
    _attach_user_sats,
    _identity_keys,
    _key_inside,
    _ollama_tail,
    _transfer_user_content,
    add_source,
    consolidate_folder_systems,
    count_skipped_ignored,
    find_systems_for_folder,
    has_user_content,
    has_user_made_content,
    history_note,
    ignored_folder_keys,
    is_ignored_folder,
    merge_generated_planets,
    refresh_system_from_code,
    workspace_key,
)
from connectors.codebase.importer import (  # noqa: F401
    CodebaseImporter,
    logger,
)
