import streamlit as st
import requests
import time
from itertools import chain
from difflib import SequenceMatcher

# Configuration
API_KEY = st.secrets["API_KEY"]  # Will be stored securely
BASE_URL = "https://api.company-information.service.gov.uk"
SLEEP_TIME = 0.25

# Initialize session
session = requests.Session()
session.auth = (API_KEY, '')

# Page config
st.set_page_config(
    page_title="Companies House Director Search",
    page_icon="🏢",
    layout="wide"
)

# Initialize session state
if 'step' not in st.session_state:
    st.session_state.step = 'input'
if 'company_data' not in st.session_state:
    st.session_state.company_data = None
if 'people_data' not in st.session_state:
    st.session_state.people_data = []
if 'current_person_idx' not in st.session_state:
    st.session_state.current_person_idx = 0
if 'match_decisions' not in st.session_state:
    st.session_state.match_decisions = {}


def api_call(endpoint, params=None):
    """API call with error handling and rate limiting."""
    time.sleep(SLEEP_TIME)
    try:
        r = session.get(f"{BASE_URL}{endpoint}", params=params, timeout=10)
        if r.status_code == 200:
            return r.json()
        elif r.status_code in [404, 416]:
            return None
        elif r.status_code == 429:
            time.sleep(2)
            return api_call(endpoint, params)
        return None
    except Exception:
        return None


def format_name_proper_order(name):
    """Convert 'SURNAME, Forename Middle' to 'Forename Middle SURNAME'."""
    prefixes = ['MR', 'MRS', 'MS', 'MISS', 'DR', 'SIR', 'LADY', 'LORD']

    name_upper = name.upper()
    for prefix in prefixes:
        name_upper = name_upper.replace(prefix + ' ', '').replace(prefix + ', ', '')

    if ',' in name_upper:
        parts = [p.strip() for p in name_upper.split(',')]
        if len(parts) == 2:
            surname = parts[0]
            forenames = parts[1]
            return f"{forenames} {surname}".strip()

    return name_upper.strip()


def normalize_name(name):
    """Remove titles and normalize."""
    prefixes = ['MR', 'MRS', 'MS', 'MISS', 'DR', 'SIR', 'LADY', 'LORD']
    parts = name.upper().replace(',', ' ').split()
    cleaned = [p for p in parts if p not in prefixes]
    return ' '.join(sorted(cleaned))


def extract_first_last(name):
    """Extract first and last name."""
    prefixes = ['MR', 'MRS', 'MS', 'MISS', 'DR', 'SIR', 'LADY', 'LORD']
    parts = name.upper().replace(',', ' ').split()
    cleaned = [p for p in parts if p not in prefixes]
    if len(cleaned) >= 2:
        return cleaned[0], cleaned[-1]
    return (cleaned[0], cleaned[0]) if cleaned else ('', '')


def name_similarity(name1, name2):
    """Calculate similarity between two names."""
    return SequenceMatcher(None, name1.upper(), name2.upper()).ratio()


def get_confidence_label(similarity):
    """Return confidence label."""
    if similarity >= 0.95:
        return "VERY HIGH ✓✓", "#28a745"
    elif similarity >= 0.85:
        return "HIGH ✓", "#5cb85c"
    elif similarity >= 0.70:
        return "MEDIUM ~", "#ffc107"
    elif similarity >= 0.50:
        return "LOW ⚠", "#ff9800"
    else:
        return "VERY LOW ✗", "#dc3545"


def get_company_info(number):
    """Fetch company details."""
    return api_call(f"/company/{number}")


def get_current_directors(number):
    """Get active directors."""
    data = api_call(f"/company/{number}/officers", {'items_per_page': 100})
    if not data or 'items' not in data:
        return []

    directors = []
    for item in data['items']:
        if item.get('resigned_on') is None:
            dob = item.get('date_of_birth', {})
            officer_id = None
            self_link = item.get('links', {}).get('officer', {}).get('appointments', '')
            if '/officers/' in self_link:
                officer_id = self_link.split('/officers/')[1].split('/')[0]

            directors.append({
                'name': item.get('name', ''),
                'dob_month': dob.get('month'),
                'dob_year': dob.get('year'),
                'roles': ['Director'],
                'officer_id': officer_id
            })
    return directors


def get_current_pscs(number):
    """Get active PSCs."""
    data = api_call(f"/company/{number}/persons-with-significant-control")
    if not data or 'items' not in data:
        return []

    pscs = []
    for item in data['items']:
        if (item.get('ceased_on') is None and
                'individual' in item.get('kind', '')):
            dob = item.get('date_of_birth', {})
            pscs.append({
                'name': item.get('name', ''),
                'dob_month': dob.get('month'),
                'dob_year': dob.get('year'),
                'roles': ['PSC'],
                'officer_id': None
            })
    return pscs


def search_officers(first, last, full_name, max_pages=10):
    """Search officers by name."""
    results = []
    queries = [
        full_name,
        f"{first} {last}",
        f"{last} {first}",
        f"{last}, {first}"
    ]

    seen_officer_ids = set()

    for query in queries:
        for page in range(max_pages):
            data = api_call("/search/officers", {
                'q': query,
                'items_per_page': 50,
                'start_index': page * 50
            })

            if not data or 'items' not in data:
                break

            for item in data['items']:
                self_link = item.get('links', {}).get('self', '')
                if '/officers/' in self_link:
                    officer_id = self_link.split('/officers/')[1].split('/')[0]
                    if officer_id and officer_id not in seen_officer_ids:
                        seen_officer_ids.add(officer_id)
                        results.append(item)

            if len(data['items']) < 50 or data.get('total_results', 0) <= (page + 1) * 50:
                break

    return results


def match_dob(officer, target_month, target_year):
    """Check if officer DOB matches target."""
    dob = officer.get('date_of_birth', {})
    return (dob.get('month') == target_month and
            dob.get('year') == target_year)


def get_all_appointments(officer_id):
    """Get all appointments for an officer."""
    appointments = []
    start = 0

    while True:
        data = api_call(f"/officers/{officer_id}/appointments", {
            'items_per_page': 50,
            'start_index': start
        })

        if not data or 'items' not in data:
            break

        appointments.extend(data['items'])

        if len(data['items']) < 50 or data.get('total_results', 0) <= start + 50:
            break

        start += 50

    return appointments


def has_insolvency(company_number):
    """Check if company has insolvency history."""
    data = api_call(f"/company/{company_number}/insolvency")
    return data is not None and 'cases' in data and len(data.get('cases', [])) > 0


def categorize_companies(appointments):
    """Categorize companies by status."""
    categories = {
        'active': [],
        'dissolved': [],
        'involuntary': [],
        'resigned': []
    }

    seen = set()
    involuntary_statuses = {'liquidation', 'administration', 'receivership',
                            'insolvency-proceedings', 'converted-closed'}

    for appt in appointments:
        appointed_to = appt.get('appointed_to', {})
        co_num = appointed_to.get('company_number')

        if not co_num or co_num in seen:
            continue

        seen.add(co_num)
        co_status = appointed_to.get('company_status', '').lower()
        co_name = appointed_to.get('company_name', 'Unknown')

        entry = f"{co_name} ({co_num})"

        if has_insolvency(co_num):
            entry += " [Insolvency history]"

        if appt.get('resigned_on'):
            categories['resigned'].append(entry)
        elif co_status in involuntary_statuses:
            categories['involuntary'].append(entry)
        elif co_status == 'dissolved':
            categories['dissolved'].append(entry)
        elif co_status == 'active':
            categories['active'].append(entry)
        else:
            categories['active'].append(entry)

    return categories


# ==================== STREAMLIT UI ====================

st.title("🏢 Companies House Director Search")
st.markdown("Search for all companies associated with directors and PSCs")

# STEP 1: Company Input
if st.session_state.step == 'input':
    st.markdown("---")
    company_number = st.text_input(
        "Enter Company Number",
        placeholder="e.g., 12345678",
        max_chars=8
    ).strip().upper()

    if st.button("Search Company", type="primary"):
        if company_number:
            with st.spinner("Fetching company information..."):
                company = get_company_info(company_number)

                if not company:
                    st.error(f"❌ Company {company_number} not found")
                else:
                    st.session_state.company_data = company

                    # Get directors and PSCs
                    directors = get_current_directors(company_number)
                    pscs = get_current_pscs(company_number)

                    # Merge and deduplicate
                    people = {}
                    for person in chain(directors, pscs):
                        key = (normalize_name(person['name']), person['dob_month'], person['dob_year'])
                        if key in people:
                            people[key]['roles'].extend(person['roles'])
                            if person.get('officer_id') and not people[key].get('officer_id'):
                                people[key]['officer_id'] = person['officer_id']
                        else:
                            people[key] = person

                    if not people:
                        st.warning("No current directors or PSCs found")
                    else:
                        # Search for matches for each person
                        with st.spinner("Searching for potential matches..."):
                            for person in people.values():
                                first, last = extract_first_last(person['name'])
                                if not first or not last:
                                    continue

                                # Get original officer ID
                                verified_ids = []
                                if person.get('officer_id'):
                                    verified_ids.append(person['officer_id'])

                                # Search for matches
                                officer_results = search_officers(
                                    first, last,
                                    format_name_proper_order(person['name'])
                                )

                                # Filter by DOB
                                potential_matches = []
                                for o in officer_results:
                                    if match_dob(o, person['dob_month'], person['dob_year']):
                                        self_link = o.get('links', {}).get('self', '')
                                        if '/officers/' in self_link:
                                            officer_id = self_link.split('/officers/')[1].split('/')[0]
                                            if officer_id not in verified_ids:
                                                potential_matches.append({
                                                    'officer': o,
                                                    'officer_id': officer_id
                                                })

                                person['potential_matches'] = potential_matches
                                person['verified_ids'] = verified_ids

                        st.session_state.people_data = list(people.values())
                        st.session_state.step = 'review_matches'
                        st.rerun()
        else:
            st.warning("Please enter a company number")

# STEP 2: Review Matches
elif st.session_state.step == 'review_matches':
    company = st.session_state.company_data
    st.success(
        f"✅ Found company: **{company.get('company_name')}** ({company.get('company_number')}) - {company.get('company_status')}")

    st.markdown("---")
    st.subheader("Review Potential Matches")

    people = st.session_state.people_data

    # Show all people with their matches
    for person_idx, person in enumerate(people):
        display_name = format_name_proper_order(person['name'])
        dob_str = f"{person['dob_month']:02d}/{person['dob_year']}" if person['dob_month'] else 'Unknown'
        roles = ' & '.join(sorted(set(person['roles'])))

        with st.expander(f"**{display_name}** (DOB: {dob_str}) - {roles}", expanded=True):
            matches = person.get('potential_matches', [])

            if not matches:
                st.info("✓ No additional matches found with same DOB")
            else:
                st.write(f"Found **{len(matches)}** potential match(es) with same DOB:")
                st.markdown("---")

                # Show each match with radio button
                for match_idx, match_info in enumerate(matches):
                    match = match_info['officer']
                    officer_id = match_info['officer_id']

                    match_name = format_name_proper_order(match.get('title', 'Unknown'))
                    match_dob = match.get('date_of_birth', {})
                    match_dob_str = f"{match_dob.get('month', '??'):02d}/{match_dob.get('year', '????')}"

                    original_name = format_name_proper_order(person['name'])
                    similarity = name_similarity(original_name, match_name)
                    confidence, color = get_confidence_label(similarity)

                    # Create unique key for this match
                    match_key = f"person{person_idx}_match{match_idx}"

                    col1, col2 = st.columns([3, 1])

                    with col1:
                        st.markdown(f"**{match_name}** (DOB: {match_dob_str})")
                        st.markdown(
                            f"<span style='color:{color}'>Confidence: {confidence} ({similarity * 100:.1f}%)</span>",
                            unsafe_allow_html=True)

                    with col2:
                        decision = st.radio(
                            "Same person?",
                            options=["Yes", "No"],
                            key=match_key,
                            horizontal=True,
                            index=0 if similarity >= 0.85 else 1  # Auto-suggest based on confidence
                        )

                        # Store decision
                        if match_key not in st.session_state.match_decisions:
                            st.session_state.match_decisions[match_key] = {
                                'person_idx': person_idx,
                                'officer_id': officer_id,
                                'decision': decision
                            }
                        else:
                            st.session_state.match_decisions[match_key]['decision'] = decision

                    st.markdown("---")

    # Confirm button
    st.markdown("---")
    if st.button("✅ Confirm Selections & View Results", type="primary"):
        st.session_state.step = 'show_results'
        st.rerun()

    if st.button("← Start Over"):
        st.session_state.clear()
        st.rerun()

# STEP 3: Show Results
elif st.session_state.step == 'show_results':
    company = st.session_state.company_data
    st.success(f"✅ **{company.get('company_name')}** ({company.get('company_number')})")

    st.markdown("---")
    st.header("📊 Results")

    people = st.session_state.people_data

    # Process each person with their confirmed matches
    for person_idx, person in enumerate(people):
        display_name = format_name_proper_order(person['name'])
        dob_str = f"{person['dob_month']:02d}/{person['dob_year']}" if person['dob_month'] else 'Unknown'
        roles = ' & '.join(sorted(set(person['roles'])))

        # Collect verified officer IDs
        verified_ids = person.get('verified_ids', []).copy()

        # Add confirmed matches
        for match_key, match_data in st.session_state.match_decisions.items():
            if match_data['person_idx'] == person_idx and match_data['decision'] == 'Yes':
                verified_ids.append(match_data['officer_id'])

        with st.expander(f"**{display_name}** (DOB: {dob_str}) - {roles}", expanded=True):
            if not verified_ids:
                st.info("No officer IDs confirmed")
                continue

            # Collect all appointments
                # Collect all appointments
                with st.spinner(f"Collecting appointments for {display_name}..."):
                    all_appointments = []
                    for officer_id in verified_ids:
                        appointments = get_all_appointments(officer_id)
                        all_appointments.extend(appointments)

                    categories = categorize_companies(all_appointments)

                # Display results
                total = sum(len(v) for v in categories.values())

                if total == 0:
                    st.info("No companies found")
                else:
                    # Show stats
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("Active", len(categories['active']))
                    with col2:
                        st.metric("Dissolved", len(categories['dissolved']))
                    with col3:
                        st.metric("Involuntary", len(categories['involuntary']))
                    with col4:
                        st.metric("Resigned", len(categories['resigned']))

                    st.markdown("---")

                    # Active companies
                    if categories['active']:
                        st.markdown(f"**✓ Active Companies ({len(categories['active'])})**")
                        for co in categories['active']:
                            st.markdown(f"- {co}")
                        st.markdown("")

                    # Dissolved companies
                    if categories['dissolved']:
                        st.markdown(f"**⊘ Dissolved Companies ({len(categories['dissolved'])})**")
                        for co in categories['dissolved']:
                            st.markdown(f"- {co}")
                        st.markdown("")

                    # Involuntary proceedings
                    if categories['involuntary']:
                        st.markdown(f"**⚠ In Involuntary Proceedings ({len(categories['involuntary'])})**")
                        for co in categories['involuntary']:
                            st.markdown(f"- {co}")
                        st.markdown("")

                    # Resigned positions
                    if categories['resigned']:
                        st.markdown(f"**← Resigned Positions ({len(categories['resigned'])})**")
                        for co in categories['resigned']:
                            st.markdown(f"- {co}")

        st.markdown("---")
        if st.button("🔄 Search Another Company", type="primary"):
            st.session_state.clear()
            st.rerun()