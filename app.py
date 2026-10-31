from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv, set_key
from docx import Document

from job_agent.ai import (
    AIServiceError,
    PROFILE_DEFAULT,
    generate_documents,
    has_openai_api_key,
    match_job,
)
from job_agent.automation import launch_prepared_application
from job_agent.db import (
    ROOT_DIR,
    create_application,
    get_job,
    get_setting,
    init_db,
    list_applications,
    list_jobs,
    save_job,
    save_match,
    set_setting,
    update_application,
)
from job_agent.discovery import discover, matches_filters
from job_agent.documents import extract_resume_text, save_uploaded_resume, write_documents
from job_agent.linkedin_discovery import (
    LinkedInImportError,
    fetch_gmail_linkedin_alerts,
    manual_linkedin_job,
    parse_linkedin_alert_email,
)


load_dotenv(ROOT_DIR / ".env")
init_db()
st.set_page_config(page_title="CareerPilot | Job Application Agent", page_icon="🧭", layout="wide")

NAV_ITEMS = [
    "Overview",
    "Job Discovery",
    "AI Job Matching",
    "Resume Builder",
    "Application Approval Queue",
    "Application Tracker",
    "Settings",
]

DEFAULT_JOB_TITLES = (
    "Data Analyst, Cybersecurity Analyst, IAM Analyst, IAM Consultant, "
    "Junior Data Engineer, Junior Data Scientist, AI/ML Engineer"
)


def _profile() -> dict:
    return get_setting(
        "candidate_profile",
        {
            "full_name": "",
            "email": "",
            "phone": "",
            "city": "",
            "linkedin": "",
            "portfolio": "",
            "profile_text": PROFILE_DEFAULT,
        },
    )


def _resume_path() -> str:
    return str(get_setting("resume_path", ""))


def _resume_text() -> str:
    path = _resume_path()
    if not path:
        raise ValueError("Upload your master resume on the Resume Builder page first.")
    return extract_resume_text(Path(path))


def _job_label(job: dict) -> str:
    return f"{job['title']} — {job['company']} ({job['location']})"


def _render_jobs_table(jobs: list[dict]) -> None:
    if not jobs:
        st.info("No jobs found yet. Run a discovery search to populate your local tracker.")
        return
    st.dataframe(
        [
            {
                "Source": job.get("source", ""),
                "Company": job.get("company", ""),
                "Job title": job.get("title", ""),
                "Location": job.get("location", ""),
                "Experience": job.get("experience_level", "Unknown"),
                "Workplace": job.get("workplace_type", "Unknown"),
                "Employment": job.get("employment_type", "Unknown"),
                "Posted": job.get("posted_at", "") or "Unknown",
                "Resume match": (
                    f"{job['match_score']}/100"
                    if job.get("match_score") is not None
                    else "Pending"
                ),
                "URL": job.get("url", ""),
            }
            for job in jobs
        ],
        use_container_width=True,
        hide_index=True,
        column_config={"URL": st.column_config.LinkColumn("Job posting")},
    )


def _auto_match_job(job_id: int, job: dict) -> str:
    if not job.get("description", "").strip():
        return "pending"
    resume_path = _resume_path()
    if not resume_path or not Path(resume_path).is_file() or not has_openai_api_key():
        return "pending"
    try:
        result = match_job(
            job["title"],
            job["company"],
            job["description"],
            extract_resume_text(Path(resume_path)),
            _profile()["profile_text"],
        )
        save_match(job_id, result)
        return "matched"
    except (AIServiceError, ValueError, OSError) as exc:
        st.warning(f"Could not match {job['title']} at {job['company']}: {exc}")
        return "pending"


def _save_discovered_jobs(jobs: list[dict]) -> tuple[int, int]:
    saved = 0
    matched = 0
    for job in jobs:
        job_id = save_job(job)
        saved += 1
        if _auto_match_job(job_id, job) == "matched":
            matched += 1
    return saved, matched


def _visible_jobs(
    source: str,
    minimum_score: int,
    search: str = "",
    filters: dict | None = None,
) -> list[dict]:
    jobs = list_jobs(search)
    if source != "All Sources":
        jobs = [job for job in jobs if job["source"].lower() == source.lower()]
    if filters:
        jobs = [job for job in jobs if matches_filters(job, filters)]
    if minimum_score:
        jobs = [
            job
            for job in jobs
            if job.get("match_score") is not None and job["match_score"] >= minimum_score
        ]
    return jobs


def _saved_job_filters(settings: dict) -> dict:
    return {
        "titles": [
            item.strip()
            for item in settings.get("titles", DEFAULT_JOB_TITLES).split(",")
            if item.strip()
        ],
        "countries": [
            item.strip()
            for item in settings.get("countries", "Germany").split(",")
            if item.strip()
        ],
        "cities": [
            item.strip()
            for item in settings.get("cities", "").split(",")
            if item.strip()
        ],
        "experience_level": settings.get(
            "experience_level", "Entry-level / Junior / Associate"
        ),
        "workplace_type": settings.get("workplace_type", "Any"),
        "full_time_only": settings.get("full_time_only", True),
        "posted_within_days": settings.get("posted_within_days"),
    }


def overview_page() -> None:
    st.title("Your job search, organized")
    st.caption("Discover public listings, compare them with your resume, and prepare applications for your review.")
    jobs = list_jobs()
    applications = list_applications()
    c1, c2, c3 = st.columns(3)
    c1.metric("Jobs tracked", len(jobs))
    c2.metric("Awaiting your approval", sum(app["status"] == "Pending approval" for app in applications))
    c3.metric("Applications submitted", sum(app["status"] == "Submitted" for app in applications))
    st.subheader("Recent opportunities")
    _render_jobs_table(jobs[:10])
    st.info("Application submission is always manual. Approval only marks a prepared application as approved; the agent never clicks Submit or Apply.")


def discovery_page() -> None:
    st.title("Job Discovery")
    st.caption("Use public Greenhouse/Lever feeds or import your own LinkedIn job-alert email. LinkedIn is never scraped or logged into.")
    settings = get_setting("discovery_settings", {})
    source = st.selectbox(
        "Job Source",
        ["All Sources", "LinkedIn", "Greenhouse", "Lever"],
        key="job_discovery_source",
    )
    minimum_score = st.slider("Minimum resume match score", 0, 100, 0, 5)
    with st.form("discovery"):
        left, right, third = st.columns(3)
        titles_text = left.text_input(
            "Job titles (comma-separated)",
            settings.get("titles", DEFAULT_JOB_TITLES),
        )
        country_text = right.text_input(
            "Country / countries",
            settings.get("countries", "Germany"),
            help="Separate multiple countries with commas.",
        )
        city_text = third.text_input("City / cities", settings.get("cities", ""))
        boards_text = left.text_input(
            "Greenhouse board slugs (company-provided)",
            settings.get("greenhouse_boards", ""),
            help="Use the public board slug from a company's Greenhouse careers URL.",
        )
        lever_text = right.text_input(
            "Lever site names (company-provided)",
            settings.get("lever_sites", ""),
            help="Use the public site name from a company's Lever careers URL.",
        )
        c1, c2, c3 = st.columns(3)
        experience_options = [
            "Any",
            "Entry-level / Junior / Associate",
            "Entry level",
            "Internship",
            "Senior",
            "Unknown",
        ]
        preferred_experience = settings.get(
            "experience_level", "Entry-level / Junior / Associate"
        )
        experience = c1.selectbox(
            "Experience level",
            experience_options,
            index=(
                experience_options.index(preferred_experience)
                if preferred_experience in experience_options
                else 1
            ),
        )
        workplace = c2.selectbox("Workplace", ["Any", "Remote", "Hybrid", "Onsite"])
        posted_days = c3.selectbox("Date posted", ["Any", "7 days", "14 days", "30 days"])
        full_time = st.checkbox(
            "Full-time only", value=settings.get("full_time_only", True)
        )
        searched = st.form_submit_button("Discover jobs", type="primary")
    if searched:
        discovery_settings = {
            "titles": titles_text,
            "countries": country_text,
            "cities": city_text,
            "greenhouse_boards": boards_text,
            "lever_sites": lever_text,
            "experience_level": experience,
            "workplace_type": workplace,
            "full_time_only": full_time,
            "posted_within_days": int(posted_days.split()[0]) if posted_days != "Any" else None,
        }
        set_setting("discovery_settings", discovery_settings)
        filters = _saved_job_filters(discovery_settings)
        if source == "LinkedIn":
            st.info("Import LinkedIn job alerts or a job URL below. LinkedIn listings are not scraped.")
        else:
            greenhouse = (
                [value.strip() for value in boards_text.split(",") if value.strip()]
                if source in {"All Sources", "Greenhouse"}
                else []
            )
            lever = (
                [value.strip() for value in lever_text.split(",") if value.strip()]
                if source in {"All Sources", "Lever"}
                else []
            )
            with st.spinner("Querying public job-board APIs..."):
                found, errors = discover(greenhouse, lever)
            filtered = [job for job in found if matches_filters(job, filters)]
            saved, matched = _save_discovered_jobs(filtered)
            st.success(
                f"Received {len(found)} listing(s); saved or updated {saved}; "
                f"matched against your resume {matched}."
            )
            for error in errors:
                st.warning(error)
            if not greenhouse and not lever:
                st.info("Add at least one public Greenhouse board slug or Lever site name.")

    st.subheader("Import LinkedIn job alerts")
    st.caption("Import only emails in a mailbox you are authorized to access. Credentials are used for this request only and are not saved.")
    with st.expander("Import an exported LinkedIn alert (.eml)"):
        alert_file = st.file_uploader("LinkedIn job-alert email", type=["eml"], key="linkedin_eml")
        if alert_file and st.button("Import jobs from email", key="import_linkedin_eml"):
            try:
                imported = parse_linkedin_alert_email(alert_file.getvalue())
                saved, _ = _save_discovered_jobs(imported)
                st.success(f"Imported or updated {saved} LinkedIn job(s). Listings without descriptions have Pending match scores.")
                if not imported:
                    st.info("No direct LinkedIn /jobs/view/ links were found in this email.")
            except (ValueError, OSError) as exc:
                st.error(str(exc))

    with st.expander("Import recent alerts from Gmail over read-only IMAP"):
        st.caption("Requires IMAP access and a Gmail app password. This reads the inbox without marking messages as read. Streamlit Cloud network policy may block IMAP access.")
        with st.form("linkedin_gmail_import"):
            gmail_user = st.text_input("Gmail address")
            gmail_password = st.text_input("Gmail app password", type="password")
            email_limit = st.selectbox("Maximum recent emails to read", [10, 20, 50, 100], index=1)
            import_gmail = st.form_submit_button("Read LinkedIn alerts")
        if import_gmail:
            try:
                with st.spinner("Reading recent LinkedIn alerts in read-only mode..."):
                    imported = fetch_gmail_linkedin_alerts(
                        gmail_user, gmail_password, email_limit
                    )
                saved, _ = _save_discovered_jobs(imported)
                st.success(f"Imported or updated {saved} LinkedIn job(s).")
                if not imported:
                    st.info("No job links were extracted from recent LinkedIn alert emails.")
            except (LinkedInImportError, ValueError) as exc:
                st.error(str(exc))

    with st.expander("Add a LinkedIn job URL and details manually"):
        with st.form("linkedin_manual_job"):
            manual_url = st.text_input("LinkedIn job URL")
            manual_title = st.text_input("Job title")
            manual_company = st.text_input("Company")
            manual_location = st.text_input("Location")
            manual_description = st.text_area(
                "Job description (optional; match remains pending if omitted)",
                max_chars=50000,
            )
            manual_has_posted = st.checkbox("I know the posting date")
            manual_posted = (
                st.date_input("Posting date") if manual_has_posted else None
            )
            manual_saved = st.form_submit_button("Save LinkedIn job")
        if manual_saved:
            try:
                job = manual_linkedin_job(
                    manual_url,
                    manual_title,
                    manual_company,
                    manual_location,
                    manual_description,
                    manual_posted.isoformat() if manual_posted else "",
                )
                _save_discovered_jobs([job])
                st.success("LinkedIn job saved. Its resume match is available when a description, resume, and API key are provided.")
            except (ValueError, OSError) as exc:
                st.error(str(exc))

    st.subheader("Saved jobs")
    query = st.text_input("Search saved jobs", key="saved_jobs_query")
    active_filters = _saved_job_filters(get_setting("discovery_settings", {}))
    all_saved = [
        job for job in list_jobs(query) if matches_filters(job, active_filters)
    ]
    eligible_unmatched = [
        job
        for job in all_saved
        if job.get("match_score") is None and job.get("description", "").strip()
    ]
    if eligible_unmatched and st.button(
        f"Match {len(eligible_unmatched)} eligible saved job(s) against my resume"
    ):
        matched_count = 0
        with st.spinner("Matching saved jobs with available descriptions..."):
            for job in eligible_unmatched:
                matched_count += _auto_match_job(job["id"], job) == "matched"
        st.success(f"Completed {matched_count} match(es); jobs without a score remain Pending.")
        all_saved = list_jobs(query)
    if minimum_score:
        pending_count = sum(job.get("match_score") is None for job in all_saved)
        st.caption(
            f"Showing only jobs with a match score of at least {minimum_score}. "
            f"{pending_count} job(s) with pending scores are excluded."
        )
    _render_jobs_table(
        _visible_jobs(source, minimum_score, query, active_filters)
    )


def matching_page() -> None:
    st.title("AI Job Matching")
    st.caption("Scores describe evidence-based fit against your provided resume, not the probability of being hired.")
    jobs = list_jobs()
    if not jobs:
        st.info("Discover or add jobs before running a match.")
        return
    chosen = st.selectbox("Select a job", jobs, format_func=_job_label, key="match_job")
    if st.button("Analyze job fit", type="primary"):
        try:
            if not chosen["description"].strip():
                st.warning("Match pending: this job has no description. Add the description before analyzing fit.")
                return
            with st.spinner("Comparing the job description with your resume..."):
                result = match_job(
                    chosen["title"],
                    chosen["company"],
                    chosen["description"],
                    _resume_text(),
                    _profile()["profile_text"],
                )
            save_match(chosen["id"], result)
            st.session_state["latest_match"] = result
            st.session_state["latest_match_job_id"] = chosen["id"]
        except (AIServiceError, ValueError, OSError) as exc:
            st.error(str(exc))
    result = st.session_state.get("latest_match")
    if result and st.session_state.get("latest_match_job_id") == chosen["id"]:
        st.metric("Explainable fit score", f"{result['score']}/100")
        st.caption(f"Priority: {result['priority']} · {result['score_explanation']}")
        left, right = st.columns(2)
        left.markdown("**Matching qualifications**")
        for item in result["matching_qualifications"]:
            left.write(f"• {item}")
        right.markdown("**Missing or unclear requirements**")
        for item in result["missing_qualifications"]:
            right.write(f"• {item}")
        st.markdown(f"**Experience compatibility:** {result['experience_compatibility']}")
        st.markdown(f"**Language requirements:** {', '.join(result['language_requirements']) or 'Not specified'}")
        st.markdown(f"**Evidence and scoring notes:** {', '.join(result['evidence']) or 'No evidence returned'}")


def resume_builder_page() -> None:
    st.title("Resume Builder")
    st.caption("Upload a private master resume, then generate editable DOCX drafts tailored to a selected role.")
    upload = st.file_uploader("Master resume (PDF or DOCX)", type=["pdf", "docx"])
    if upload and st.button("Save master resume"):
        try:
            path = save_uploaded_resume(upload.name, upload.getvalue())
            set_setting("resume_path", str(path))
            st.success(f"Resume saved locally ({path.name}).")
        except (OSError, ValueError) as exc:
            st.error(str(exc))
    current_resume = _resume_path()
    if current_resume and Path(current_resume).is_file():
        st.success(f"Current private resume: {Path(current_resume).name}")
    jobs = list_jobs()
    if not jobs:
        st.info("Discover jobs before generating tailored documents.")
        return
    chosen = st.selectbox("Select a target job", jobs, format_func=_job_label, key="builder_job")
    language = st.selectbox("Document language", ["English", "German"])
    if st.button("Generate tailored DOCX drafts", type="primary"):
        try:
            with st.spinner("Generating drafts. Review every claim before using them."):
                payload = generate_documents(
                    chosen["title"],
                    chosen["company"],
                    chosen["location"],
                    chosen["description"],
                    _resume_text(),
                    _profile()["profile_text"],
                    language,
                )
                resume_path, cover_path = write_documents(chosen["id"], chosen["company"], payload)
            st.session_state["generated_docs"] = {
                "job_id": chosen["id"],
                "resume": str(resume_path),
                "cover": str(cover_path),
            }
            st.session_state["generated_payload"] = payload
            st.success("Drafts saved locally. Check facts and formatting before use.")
        except (AIServiceError, ValueError, OSError) as exc:
            st.error(str(exc))
    generated = st.session_state.get("generated_docs")
    if generated and generated["job_id"] == chosen["id"]:
        payload = st.session_state.get("generated_payload", {})
        st.subheader("Cover letter preview")
        st.write(payload.get("cover_letter", ""))
        with open(generated["resume"], "rb") as file:
            st.download_button("Download tailored resume", file, file_name=Path(generated["resume"]).name)
        with open(generated["cover"], "rb") as file:
            st.download_button("Download cover letter", file, file_name=Path(generated["cover"]).name)
        if st.button("Add to approval queue"):
            application_id = create_application(
                chosen["id"],
                generated["resume"],
                generated["cover"],
            )
            update_application(
                application_id,
                status="Pending approval",
                resume_version=Path(generated["resume"]).name,
                cover_letter_version=Path(generated["cover"]).name,
            )
            st.success("Added to the approval queue. Nothing has been submitted.")


def approval_page() -> None:
    st.title("Application Approval Queue")
    st.caption("Review the application preview before approving. Approval does not submit the application.")
    applications = [app for app in list_applications() if app["status"] in {"Pending approval", "Approved"}]
    if not applications:
        st.info("No applications are waiting. Create drafts from the Resume Builder.")
        return
    selected = st.selectbox(
        "Application",
        applications,
        format_func=lambda app: f"{app['title']} — {app['company']} · {app['status']}",
    )
    job = get_job(selected["job_id"])
    if not job:
        st.error("The job associated with this application no longer exists.")
        return
    st.subheader("Application preview")
    st.write(f"**Role:** {job['title']} at {job['company']}")
    st.write(f"**Location:** {job['location']}")
    st.write(f"**Posting:** {job['url']}")
    st.write(f"**Fit score:** {selected.get('match_score') if selected.get('match_score') is not None else 'Not analyzed'}")
    profile = _profile()
    st.write(f"**Applicant:** {profile.get('full_name') or 'Name not provided'}")
    st.write(f"**Email:** {profile.get('email') or 'Not provided'}")
    st.write(f"**Phone:** {profile.get('phone') or 'Not provided'}")
    st.write(f"**Location:** {profile.get('city') or 'Not provided'}")
    st.write(f"**LinkedIn:** {profile.get('linkedin') or 'Not provided'}")
    st.write(f"**Resume draft:** {Path(selected['resume_path']).name if selected['resume_path'] else 'Not attached'}")
    st.write(f"**Cover letter draft:** {Path(selected['cover_letter_path']).name if selected['cover_letter_path'] else 'Not attached'}")
    for label, document_path in (
        ("Resume", selected["resume_path"]),
        ("Cover letter", selected["cover_letter_path"]),
    ):
        if document_path and Path(document_path).is_file():
            with st.expander(f"Preview {label.lower()}"):
                document = Document(document_path)
                st.text("\n".join(paragraph.text for paragraph in document.paragraphs))
    st.warning("Screening questions cannot be answered from the available profile yet. Review them yourself in the browser. No final submission control is automated.")
    if selected["status"] == "Pending approval":
        if st.button("Approve this application for manual submission", type="primary"):
            update_application(selected["id"], status="Approved")
            st.success("Approval recorded for this application. You remain responsible for manually submitting it.")
            st.rerun()
    else:
        st.success("Approved for this application; final submission remains manual.")
        if st.button("Open posting and fill recognized fields"):
            try:
                launch_prepared_application(
                    job["url"],
                    profile,
                    selected["resume_path"],
                    selected["cover_letter_path"],
                )
                st.success("A browser preparation window was started. Check the browser and review every field; the agent will not submit.")
            except OSError as exc:
                st.error(f"Could not start browser preparation: {exc}")


def tracker_page() -> None:
    st.title("Application Tracker")
    applications = list_applications()
    if not applications:
        st.info("Your application tracker is empty.")
        return
    st.dataframe(
        [
            {
                "Company": app["company"],
                "Job title": app["title"],
                "Location": app["location"],
                "Match": app["match_score"],
                "Application date": app["application_date"],
                "Status": app["status"],
                "Resume version": app["resume_version"],
                "Cover letter version": app["cover_letter_version"],
                "Recruiter response": app["recruiter_response"],
                "Interview status": app["interview_status"],
            }
            for app in applications
        ],
        use_container_width=True,
        hide_index=True,
    )
    selected = st.selectbox(
        "Update a tracked application",
        applications,
        format_func=lambda app: f"{app['title']} — {app['company']} (#{app['id']})",
    )
    with st.form("tracker_update"):
        status_options = {
            "Pending approval": ["Pending approval", "Rejected", "Withdrawn"],
            "Approved": ["Approved", "Submitted", "Rejected", "Withdrawn"],
            "Submitted": ["Submitted", "Interviewing", "Rejected", "Offer", "Withdrawn"],
            "Interviewing": ["Interviewing", "Rejected", "Offer", "Withdrawn"],
            "Rejected": ["Rejected"],
            "Offer": ["Offer"],
            "Withdrawn": ["Withdrawn"],
        }
        statuses = status_options.get(selected["status"], [selected["status"]])
        status = st.selectbox("Status", statuses, index=statuses.index(selected["status"]) if selected["status"] in statuses else 0)
        app_date = st.date_input(
            "Application date",
            value=date.fromisoformat(selected["application_date"]) if selected["application_date"] else date.today(),
        )
        response = st.text_area("Recruiter response", value=selected["recruiter_response"])
        interview = st.text_input("Interview status", value=selected["interview_status"])
        saved = st.form_submit_button("Save tracking update")
    if saved:
        update_application(
            selected["id"],
            status=status,
            application_date=app_date.isoformat() if status == "Submitted" else selected["application_date"],
            recruiter_response=response,
            interview_status=interview,
        )
        st.success("Application tracker updated.")
        st.rerun()


def settings_page() -> None:
    st.title("Settings")
    st.caption("Personal profile and API settings are stored locally. API keys are saved only in the ignored .env file.")
    profile = _profile()
    with st.form("profile"):
        full_name = st.text_input("Full name", value=profile.get("full_name", ""))
        email = st.text_input("Email", value=profile.get("email", ""))
        phone = st.text_input("Phone", value=profile.get("phone", ""))
        city = st.text_input("City / preferred location", value=profile.get("city", ""))
        linkedin = st.text_input("LinkedIn URL", value=profile.get("linkedin", ""))
        portfolio = st.text_input("Portfolio URL", value=profile.get("portfolio", ""))
        profile_text = st.text_area("Verified background and skills", value=profile.get("profile_text", PROFILE_DEFAULT), height=180)
        profile_saved = st.form_submit_button("Save profile")
    if profile_saved:
        set_setting(
            "candidate_profile",
            {
                "full_name": full_name,
                "email": email,
                "phone": phone,
                "city": city,
                "linkedin": linkedin,
                "portfolio": portfolio,
                "profile_text": profile_text,
            },
        )
        st.success("Profile saved locally.")

    st.subheader("OpenAI API")
    st.write("API key status:", "Configured" if has_openai_api_key() else "Not configured")
    with st.form("api_key"):
        key_value = st.text_input("OpenAI API key", type="password", help="Never stored in SQLite or displayed after saving.")
        model = st.text_input("OpenAI model", value=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
        key_saved = st.form_submit_button("Save API configuration")
    if key_saved:
        env_path = ROOT_DIR / ".env"
        env_path.touch(exist_ok=True)
        if key_value.strip():
            set_key(str(env_path), "OPENAI_API_KEY", key_value.strip())
            os.environ["OPENAI_API_KEY"] = key_value.strip()
        set_key(str(env_path), "OPENAI_MODEL", model.strip() or "gpt-4o-mini")
        os.environ["OPENAI_MODEL"] = model.strip() or "gpt-4o-mini"
        st.success("API configuration saved locally. The key is not shown again.")

    st.subheader("Daily discovery (optional)")
    st.write("Windows Task Scheduler can discover and save public-feed listings daily. It never submits applications.")
    script = ROOT_DIR / "scripts" / "install_daily_task.ps1"
    st.code(f'powershell -ExecutionPolicy Bypass -File "{script}"')
    st.caption("Configure public board names in Job Discovery first. The scheduled job uses the local database settings.")


page = st.sidebar.radio("Navigation", NAV_ITEMS)
if page == "Overview":
    overview_page()
elif page == "Job Discovery":
    discovery_page()
elif page == "AI Job Matching":
    matching_page()
elif page == "Resume Builder":
    resume_builder_page()
elif page == "Application Approval Queue":
    approval_page()
elif page == "Application Tracker":
    tracker_page()
else:
    settings_page()
