"""
app.py — Streamlit Competitive Intelligence Orchestrator Dashboard.
"""

import json
import streamlit as st
from datetime import datetime

from src.core.db import (
    init_db, get_session, Company, CompanyProfile, Competitor, Comparison, Job, BudgetLog, PageSnapshot
)
from src.reports.builder import build_all_reports
from src.alerts.detector import detect_snapshot_changes

# Initialize DB on load
init_db()

st.set_page_config(
    page_title="Competitor Intelligence Orchestrator",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for rich aesthetics
st.markdown("""
<style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        background: linear-gradient(90deg, #3B82F6 0%, #8B5CF6 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.5rem;
    }
    .card {
        background-color: #1E293B;
        padding: 1.2rem;
        border-radius: 10px;
        border: 1px solid #334155;
        margin-bottom: 1rem;
    }
    .badge-unavailable {
        background-color: #EF4444;
        color: white;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 0.8rem;
    }
    .badge-tier {
        background-color: #3B82F6;
        color: white;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 0.8rem;
    }
</style>
""", unsafe_allow_html=True)

db = get_session()

# ---------------------------------------------------------------------------
# Sidebar: Company Selector & Navigation
# ---------------------------------------------------------------------------
st.sidebar.markdown("## ⚡ Intelligence Hub")

companies = db.query(Company).order_by(Company.name).all()
company_options = {f"{c.name} ({c.slug})": c.id for c in companies}

selected_company_id = None
if company_options:
    selected_label = st.sidebar.selectbox("Select Target Company", list(company_options.keys()))
    selected_company_id = company_options[selected_label]
else:
    st.sidebar.info("No companies loaded yet. Add your first company below!")

st.sidebar.markdown("---")
page_mode = st.sidebar.radio(
    "Navigation",
    [
        "🏢 Profile & Checkpoints",
        "🎯 Candidate Discovery",
        "📊 1v1 Comparison Matrix",
        "📄 Report Generator",
        "🔔 Weekly Change Digest",
        "💰 Budget & Usage"
    ]
)

# Add Company by URL Form
st.sidebar.markdown("---")
with st.sidebar.expander("➕ Add New Company by URL"):
    with st.form("add_company_form"):
        new_url = st.text_input("Website URL", placeholder="https://example.com")
        new_name = st.text_input("Company Name (optional)")
        submitted = st.form_submit_button("Track Company")

        if submitted and new_url:
            import uuid
            company_id = str(uuid.uuid4())
            slug = new_url.replace("https://", "").replace("http://", "").split("/")[0].replace(".", "-")
            comp = Company(
                id=company_id,
                slug=slug,
                name=new_name or slug,
                url=new_url,
                status="pending_profile"
            )
            db.add(comp)
            job = Job(
                id=str(uuid.uuid4()),
                company_id=company_id,
                job_type="profile_extraction",
                status="pending"
            )
            db.add(job)
            db.commit()
            st.success(f"Added {slug}! Checkpoint 1 active.")
            st.rerun()

# ---------------------------------------------------------------------------
# Main Panel Rendering
# ---------------------------------------------------------------------------

if selected_company_id:
    company = db.query(Company).filter(Company.id == selected_company_id).first()
    profile = db.query(CompanyProfile).filter(CompanyProfile.company_id == selected_company_id).first()

    st.markdown(f"<div class='main-header'>{company.name}</div>", unsafe_allow_html=True)
    st.caption(f"Domain: {company.url} | Status: `{company.status}`")

    # --- Mode 1: Profile & Checkpoint 1 ---
    if page_mode == "🏢 Profile & Checkpoints":
        st.subheader("Checkpoint 1: Target Company Profile Confirmation")

        if not profile:
            st.warning("Company profile extraction is pending or not yet generated.")
            if st.button("Generate Default Draft Profile"):
                import uuid
                profile = CompanyProfile(
                    id=str(uuid.uuid4()),
                    company_id=company.id,
                    name=company.name,
                    industry_label="Software & SaaS",
                    price_band="Freemium / Paid Tier",
                    hq_city="San Francisco",
                    hq_country="USA"
                )
                db.add(profile)
                db.commit()
                st.rerun()
        else:
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("### Extracted Profile")
                st.write(f"**Industry Label:** {profile.industry_label or 'N/A'}")
                st.write(f"**Price Band:** {profile.price_band or 'N/A'}")
                st.write(f"**HQ City:** {profile.hq_city or 'N/A'}")
                st.write(f"**HQ Country:** {profile.hq_country or 'N/A'}")
                st.write(f"**Confirmed At:** {profile.confirmed_at or 'Not Confirmed'}")

            with col2:
                st.markdown("### Confirm / Overrides")
                with st.form("confirm_profile_form"):
                    override_industry = st.text_input("Override Industry", value=profile.industry_label or "")
                    override_hq = st.text_input("Override HQ City", value=profile.hq_city or "")
                    confirm_btn = st.form_submit_button("Confirm Profile (Pass Checkpoint 1)")

                    if confirm_btn:
                        profile.industry_label = override_industry
                        profile.hq_city = override_hq
                        profile.confirmed_at = datetime.utcnow()
                        company.status = "confirmed"
                        db.commit()
                        st.success("Profile confirmed! You can now run candidate discovery.")
                        st.rerun()

    # --- Mode 2: Candidate Discovery & Checkpoint 2 ---
    elif page_mode == "🎯 Candidate Discovery":
        st.subheader("Checkpoint 2: Discover & Approve Candidate Competitors")

        if not profile or not profile.confirmed_at:
            st.error("🔒 Profile must be confirmed in Checkpoint 1 before running discovery.")
        else:
            if st.button("🔍 Run Discovery"):
                import uuid
                # Create candidate competitors for demo
                c1 = Competitor(id=str(uuid.uuid4()), company_id=company.id, name="Competitor Alpha", domain="alpha.com", tier="direct", score=0.92)
                c2 = Competitor(id=str(uuid.uuid4()), company_id=company.id, name="Competitor Beta", domain="beta.io", tier="indirect", score=0.75)
                db.add_all([c1, c2])
                db.commit()
                st.success("Discovered candidate competitors!")
                st.rerun()

            competitors = db.query(Competitor).filter(Competitor.company_id == company.id).all()
            if competitors:
                tier_filter = st.multiselect("Filter by Tier", ["direct", "indirect", "perceived"], default=["direct", "indirect", "perceived"])
                filtered = [c for c in competitors if c.tier in tier_filter or not c.tier]

                st.markdown(f"Found **{len(filtered)}** candidates:")
                for c in filtered:
                    col1, col2, col3 = st.columns([3, 1, 1])
                    with col1:
                        st.write(f"**{c.name}** (`{c.domain}`)")
                    with col2:
                        st.markdown(f"<span class='badge-tier'>{c.tier}</span> Score: {c.score:.2f}", unsafe_allow_html=True)
                    with col3:
                        approved = st.checkbox("Approve", value=c.approved, key=f"app_{c.id}")
                        if approved != c.approved:
                            c.approved = approved
                            db.commit()
                            st.toast(f"Updated approval for {c.name}")
            else:
                st.info("No candidates discovered yet.")

    # --- Mode 3: 1v1 Comparison Matrix ---
    elif page_mode == "📊 1v1 Comparison Matrix":
        st.subheader("1v1 Side-by-Side Comparison Matrix")
        competitors = db.query(Competitor).filter(Competitor.company_id == company.id, Competitor.approved == True).all()

        if not competitors:
            st.warning("No approved competitors available for comparison. Approve competitors in Checkpoint 2.")
        else:
            comp_name = st.selectbox("Select Competitor to Compare", [c.name for c in competitors])
            comp_obj = next(c for c in competitors if c.name == comp_name)

            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f"### 🔵 {company.name} (Primary)")
                st.write(f"**URL:** [{company.url}]({company.url})")
                st.write(f"**Price Band:** {profile.price_band if profile else 'N/A'}")
                st.write(f"**HQ:** {profile.hq_city if profile else 'N/A'}")

            with col2:
                st.markdown(f"### 🔴 {comp_obj.name} (Competitor)")
                st.write(f"**URL:** [{comp_obj.domain}](https://{comp_obj.domain})")
                st.write(f"**Tier:** {comp_obj.tier}")
                st.markdown("<span class='badge-unavailable'>Data Unavailable</span> (Pending full deep crawl)", unsafe_allow_html=True)

    # --- Mode 4: Report Generator ---
    elif page_mode == "📄 Report Generator":
        st.subheader("Multi-Format Report Generator")
        st.write("Generate dated Markdown, JSON, PDF, and PowerPoint reports.")

        if st.button("🚀 Build All Reports"):
            paths = build_all_reports(company.id)
            st.success("Reports successfully built!")
            st.json(paths)

    # --- Mode 5: Weekly Change Digest ---
    elif page_mode == "🔔 Weekly Change Digest":
        st.subheader("Weekly Change Detection & Digest")
        changes = detect_snapshot_changes(company.id)

        if changes:
            st.warning(f"Detected **{len(changes)}** change events across snapshots:")
            for ch in changes:
                st.write(f"- **{ch['change_type']}** on `{ch['url']}` ({ch['detected_at']})")
        else:
            st.info("No changes detected since last collection.")

    # --- Mode 6: Budget & Usage ---
    elif page_mode == "💰 Budget & Usage":
        st.subheader("LLM Budget & Request Usage")

        today = datetime.utcnow().strftime("%Y-%m-%d")
        logs = db.query(BudgetLog).filter(BudgetLog.company_id == company.id).all()

        total_reqs = sum(l.requests or 0 for l in logs)
        total_tokens = sum((l.tokens_in or 0) + (l.tokens_out or 0) for l in logs)

        col1, col2, col3 = st.columns(3)
        col1.metric("Requests Today", total_reqs, f"{500 - total_reqs} remaining")
        col2.metric("Tokens Consumed", total_tokens, f"{500000 - total_tokens} remaining")
        col3.metric("Smart vs Bulk", f"{total_reqs} / 0")

else:
    st.info("Please select or add a target company from the sidebar to begin.")

db.close()
