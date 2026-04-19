from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class VenueCatalogEntry:
    display_name: str
    venue_type: str
    tier: str
    aliases: tuple[str, ...] = ()


def _conference(display_name: str, *aliases: str) -> VenueCatalogEntry:
    return VenueCatalogEntry(
        display_name=display_name,
        venue_type="conference",
        tier="ccf_a",
        aliases=tuple(str(item).strip() for item in aliases if str(item).strip()),
    )


def _journal(display_name: str, *aliases: str) -> VenueCatalogEntry:
    return VenueCatalogEntry(
        display_name=display_name,
        venue_type="journal",
        tier="ccf_a",
        aliases=tuple(str(item).strip() for item in aliases if str(item).strip()),
    )


_CCF_A_VENUES: tuple[VenueCatalogEntry, ...] = (
    _conference(
        "ACM International Conference on Architectural Support for Programming Languages and Operating Systems",
        "ASPLOS",
    ),
    _conference("USENIX Conference on File and Storage Technologies", "FAST"),
    _conference("IEEE International Symposium on High-Performance Computer Architecture", "HPCA"),
    _conference("International Symposium on Computer Architecture", "ISCA"),
    _conference("IEEE/ACM International Symposium on Microarchitecture", "MICRO"),
    _conference("International Conference for High Performance Computing, Networking, Storage and Analysis", "SC"),
    _conference("USENIX Annual Technical Conference", "USENIX ATC", "ATC"),
    _conference("Design Automation Conference", "DAC"),
    _conference("IEEE/ACM International Conference on Computer-Aided Design", "ICCAD"),
    _conference("International Conference on Embedded Software", "EMSOFT"),
    _conference("IEEE Real-Time and Embedded Technology and Applications Symposium", "RTAS"),
    _conference("ACM Special Interest Group on Data Communication", "SIGCOMM"),
    _conference("ACM International Conference on Mobile Computing and Networking", "MobiCom"),
    _conference("IEEE International Conference on Computer Communications", "INFOCOM"),
    _conference("USENIX Symposium on Networked Systems Design and Implementation", "NSDI"),
    _conference("ACM Conference on Computer and Communications Security", "CCS"),
    _conference("Annual International Conference on the Theory and Applications of Cryptographic Techniques", "EUROCRYPT"),
    _conference(
        "IEEE Symposium on Security and Privacy",
        "S&P",
        "IEEE S&P",
        "IEEE Symposium on Security & Privacy",
        "Oakland",
    ),
    _conference("Annual International Cryptology Conference", "CRYPTO"),
    _conference("USENIX Security Symposium", "USENIX Security"),
    _conference("Network and Distributed System Security Symposium", "NDSS"),
    _conference("ACM SIGPLAN Conference on Programming Language Design and Implementation", "PLDI"),
    _conference(
        "ACM SIGPLAN-SIGACT Symposium on Principles of Programming Languages",
        "POPL",
    ),
    _conference("ACM International Conference on the Foundations of Software Engineering", "FSE"),
    _conference(
        "ACM SIGPLAN International Conference on Object-Oriented Programming, Systems, Languages, and Applications",
        "OOPSLA",
    ),
    _conference("International Conference on Computer Aided Verification", "CAV"),
    _conference("International Conference on Software Engineering", "ICSE"),
    _conference("International Symposium on Software Testing and Analysis", "ISSTA"),
    _conference("IEEE/ACM International Conference on Automated Software Engineering", "ASE"),
    _conference("European Conference on Object-Oriented Programming", "ECOOP"),
    _conference("European Joint Conferences on Theory and Practice of Software", "ETAPS"),
    _conference("International Conference on Management of Data", "SIGMOD", "ACM SIGMOD"),
    _conference("ACM SIGKDD Conference on Knowledge Discovery and Data Mining", "SIGKDD", "KDD"),
    _conference("International ACM SIGIR Conference on Research and Development in Information Retrieval", "SIGIR"),
    _conference("International Conference on Very Large Data Bases", "VLDB"),
    _conference("IEEE International Conference on Data Engineering", "ICDE"),
    _conference("Annual ACM Symposium on Theory of Computing", "STOC"),
    _conference("IEEE Annual Symposium on Foundations of Computer Science", "FOCS"),
    _conference("ACM-SIAM Symposium on Discrete Algorithms", "SODA"),
    _conference("ACM International Conference on Multimedia", "ACM MM", "Multimedia"),
    _conference("Annual Conference on Computer Graphics and Interactive Techniques", "SIGGRAPH"),
    _conference("IEEE Virtual Reality", "VR", "IEEE VR"),
    _conference("IEEE Visualization Conference", "VIS", "IEEE VIS"),
    _conference("AAAI Conference on Artificial Intelligence", "AAAI"),
    _conference(
        "Conference on Neural Information Processing Systems",
        "NeurIPS",
        "NIPS",
        "Neural Information Processing Systems",
        "Advances in Neural Information Processing Systems",
    ),
    _conference("Annual Meeting of the Association for Computational Linguistics", "ACL"),
    _conference("IEEE/CVF Conference on Computer Vision and Pattern Recognition", "CVPR"),
    _conference("IEEE/CVF International Conference on Computer Vision", "ICCV"),
    _conference("International Conference on Machine Learning", "ICML"),
    _conference("International Joint Conference on Artificial Intelligence", "IJCAI"),
    _conference("ACM Conference on Computer Supported Cooperative Work and Social Computing", "CSCW"),
    _conference("CHI Conference on Human Factors in Computing Systems", "CHI"),
    _conference("ACM Symposium on User Interface Software and Technology", "UIST"),
    _conference("The Web Conference", "WWW", "International World Wide Web Conference"),
    _journal("IEEE Journal of Solid-State Circuits", "JSSC"),
    _journal("Proceedings of the IEEE"),
    _journal("IEEE/ACM Transactions on Networking", "ToN", "TON"),
    _journal("IEEE Transactions on Information Theory", "TIT"),
    _journal("IEEE Transactions on Image Processing", "TIP"),
    _journal("IEEE Transactions on Computers", "TC"),
    _journal("ACM Transactions on Graphics", "TOG"),
    _journal("ACM Transactions on Database Systems", "TODS"),
    _journal("ACM Transactions on Computer Systems", "TOCS"),
    _journal("Journal of Cryptology", "JoC"),
    _journal("IEEE Transactions on Dependable and Secure Computing", "TDSC"),
    _journal("IEEE Transactions on Information Forensics and Security", "TIFS"),
    _journal("ACM Transactions on Programming Languages and Systems", "TOPLAS"),
    _journal("ACM Transactions on Software Engineering and Methodology", "TOSEM"),
    _journal("IEEE Transactions on Software Engineering", "TSE"),
    _journal("IEEE Transactions on Services Computing", "TSC"),
    _journal("ACM Transactions on Information Systems", "TOIS"),
    _journal("IEEE Transactions on Knowledge and Data Engineering", "TKDE"),
    _journal("The VLDB Journal", "VLDBJ", "VLDB Journal"),
    _journal("SIAM Journal on Computing", "SICOMP"),
    _journal("ACM Transactions on Algorithms", "TALG"),
    _journal("ACM Transactions on Computational Logic", "TOCL"),
    _journal("IEEE Transactions on Visualization and Computer Graphics", "TVCG"),
    _journal("International Journal of Computer Vision", "IJCV"),
    _journal("Artificial Intelligence", "AI", "Artificial Intelligence Journal"),
    _journal("IEEE Transactions on Pattern Analysis and Machine Intelligence", "TPAMI", "PAMI"),
    _journal("Journal of Machine Learning Research", "JMLR"),
    _journal("ACM Transactions on Computer-Human Interaction", "TOCHI"),
    _journal("Human-Computer Interaction", "HCI"),
    _journal("Bioinformatics"),
    _journal("IEEE/ACM Transactions on Computational Biology and Bioinformatics", "TCBB"),
    _journal("IEEE Transactions on Medical Imaging", "TMI"),
    _journal("ACM Computing Surveys", "CSUR"),
)

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_AUXILIARY_VENUE_MARKERS: tuple[str, ...] = (
    " workshop ",
    " workshops ",
    " companion ",
    " extended abstract ",
    " extended abstracts ",
    " poster ",
    " posters ",
    " demo ",
    " demos ",
    " doctoral consortium ",
    " tutorial ",
    " tutorials ",
    " forum ",
    " challenge ",
    " challenges ",
    " short paper ",
    " short papers ",
)
_ALIAS_TO_ENTRY: dict[str, VenueCatalogEntry] = {}
_LONG_ALIAS_CANDIDATES: list[tuple[str, VenueCatalogEntry]] = []

for _entry in _CCF_A_VENUES:
    seen_aliases: set[str] = set()
    for alias in (_entry.display_name, *_entry.aliases):
        normalized = _NON_ALNUM_RE.sub(" ", str(alias or "").lower()).strip()
        if not normalized or normalized in seen_aliases:
            continue
        seen_aliases.add(normalized)
        _ALIAS_TO_ENTRY.setdefault(normalized, _entry)
        if len(normalized) >= 8:
            _LONG_ALIAS_CANDIDATES.append((normalized, _entry))

_LONG_ALIAS_CANDIDATES.sort(key=lambda item: len(item[0]), reverse=True)


def normalize_venue_name(value: str | None) -> str:
    return _NON_ALNUM_RE.sub(" ", str(value or "").lower()).strip()


def normalize_venue_type(value: str | None) -> str:
    normalized = normalize_venue_name(value)
    if normalized in {"conference", "proceedings", "conference proceedings"}:
        return "conference"
    if normalized in {"journal", "magazine"}:
        return "journal"
    if normalized in {"repository", "preprint"}:
        return "repository"
    return normalized or "unknown"


def _contains_auxiliary_marker(value: str) -> bool:
    padded = f" {value} "
    return any(marker in padded for marker in _AUXILIARY_VENUE_MARKERS)


def lookup_venue_entry(venue_name: str | None) -> VenueCatalogEntry | None:
    normalized = normalize_venue_name(venue_name)
    if not normalized:
        return None
    direct = _ALIAS_TO_ENTRY.get(normalized)
    if direct is not None:
        return direct
    padded = f" {normalized} "
    for alias, entry in _LONG_ALIAS_CANDIDATES:
        alias_padded = f" {alias} "
        if _contains_auxiliary_marker(normalized):
            continue
        if alias_padded in padded:
            return entry
    return None


def venue_tier_for_name(venue_name: str | None) -> str | None:
    entry = lookup_venue_entry(venue_name)
    return entry.tier if entry is not None else None


def classify_venue_type(raw_type: str | None, venue_name: str | None = None) -> str:
    normalized = normalize_venue_type(raw_type)
    if normalized in {"conference", "journal", "repository"}:
        return normalized
    entry = lookup_venue_entry(venue_name)
    if entry is not None:
        return entry.venue_type
    return normalized or "unknown"


def _matches_named_venue(venue_name: str | None, venue_names: list[str] | None) -> bool:
    if not venue_names:
        return True
    normalized_venue = normalize_venue_name(venue_name)
    entry = lookup_venue_entry(venue_name)
    venue_aliases = {normalized_venue}
    if entry is not None:
        venue_aliases.add(normalize_venue_name(entry.display_name))
        venue_aliases.update(normalize_venue_name(alias) for alias in entry.aliases)
    for requested_name in venue_names:
        normalized_requested = normalize_venue_name(requested_name)
        if not normalized_requested:
            continue
        if normalized_requested in venue_aliases:
            return True
        if len(normalized_requested) >= 8:
            padded_requested = f" {normalized_requested} "
            if any(
                padded_requested in f" {candidate} " or f" {candidate} " in padded_requested
                for candidate in venue_aliases
            ):
                return True
    return False


def matches_venue_filter(
    venue_name: str | None,
    *,
    raw_venue_type: str | None = None,
    venue_tier: str = "all",
    venue_type: str = "all",
    venue_names: list[str] | None = None,
) -> bool:
    normalized_tier = normalize_venue_name(venue_tier) or "all"
    normalized_type = normalize_venue_type(venue_type) or "all"
    actual_type = classify_venue_type(raw_venue_type, venue_name)
    actual_tier = venue_tier_for_name(venue_name) or "unknown"

    if normalized_tier == "ccf a":
        normalized_tier = "ccf_a"
    if normalized_tier == "ccf_a" and actual_tier != "ccf_a":
        return False
    if normalized_type in {"conference", "journal"} and actual_type != normalized_type:
        return False
    return _matches_named_venue(venue_name, venue_names)
