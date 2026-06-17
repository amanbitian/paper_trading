from __future__ import annotations

from decimal import Decimal
import re

import streamlit as st

from api_client import (
    clear_auth_state,
    debug_auth_enabled,
    format_inr,
    get,
    log_page_load,
    post,
    start_timer,
)
from ui import info_banner, load_global_css, metric_grid, page_header, status_badge


PAGE_STARTED_AT = start_timer()
st.set_page_config(page_title="Paper Trading App", page_icon="PT", layout="wide")
load_global_css()
page_header(
    "Paper Trading App",
    "This is a paper trading and educational tool. It does not provide financial advice.",
    right_badge=status_badge("Local-first", "info"),
)

if "token" not in st.session_state:
    st.session_state.token = None
if "auth_view" not in st.session_state:
    st.session_state.auth_view = "Login"
if "registration_notice" not in st.session_state:
    st.session_state.registration_notice = None


def login_form() -> None:
    notice = st.session_state.get("registration_notice")
    if notice:
        info_banner(notice, "success")
        st.session_state.registration_notice = None
    with st.container(border=True):
        with st.form("login_form"):
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Login")
    if submitted:
        result = post("/auth/login", {"email": email, "password": password})
        if result:
            st.session_state.token = result["access_token"]
            info_banner("Logged in.", "success")
            st.rerun()


USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")


def registration_errors(name: str, user_name: str, email: str, password: str) -> list[str]:
    errors = []
    if len(name.strip()) < 2:
        errors.append("Name is required.")
    if len(user_name.strip()) < 3:
        errors.append("Username must be at least 3 characters.")
    if len(user_name.strip()) > 30:
        errors.append("Username must be 30 characters or fewer.")
    if user_name.strip() and not USERNAME_PATTERN.fullmatch(user_name.strip()):
        errors.append("Username can contain only letters, numbers, and underscores.")
    if not email.strip():
        errors.append("Email is required.")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    if len(password) > 72:
        errors.append("Password must be 72 characters or fewer.")
    if len(password.encode("utf-8")) > 72:
        errors.append("Password must be 72 bytes or fewer.")
    return errors


def show_registration_errors(errors: list[str]) -> None:
    st.error("Registration failed. Please fix:\n" + "\n".join(f"- {error}" for error in errors))


def register_form() -> None:
    with st.container(border=True):
        with st.form("register_form"):
            name = st.text_input("Name", placeholder="Aman Anand")
            user_name = st.text_input(
                "Username",
                key="register_user_name",
                placeholder="aman_anand",
                help="Public handle shown on the platform. Use 3-30 letters, numbers, or underscores.",
            )
            email = st.text_input("Email", key="register_email")
            password = st.text_input(
                "Password",
                type="password",
                key="register_password",
                help="Use 8 to 72 characters.",
            )
            st.caption("Password requirement: 8 to 72 characters.")
            starting_cash = st.number_input("Starting paper cash", min_value=0.0, value=1000000.0, step=10000.0)
            risk_profile = st.selectbox("Risk profile", ["conservative", "moderate", "aggressive"], index=1)
            submitted = st.form_submit_button("Register")
    if submitted:
        validation_errors = registration_errors(name, user_name, email, password)
        if validation_errors:
            show_registration_errors(validation_errors)
            return
        result = post(
            "/auth/register",
            {
                "name": name.strip(),
                "user_name": user_name.strip().lower(),
                "email": email.strip(),
                "password": password,
                "starting_cash": str(Decimal(str(starting_cash))),
                "risk_profile": risk_profile,
            },
            return_error=True,
            show_error=False,
        )
        if result and not result.get("error"):
            st.session_state.auth_view = "Login"
            st.session_state.login_email = result["email"]
            st.session_state.registration_notice = "Registered successfully. Log in with your email and password."
            st.rerun()
        if result and result.get("error"):
            message = str(result.get("message", "Registration failed."))
            lower_message = message.lower()
            if "email already registered" in lower_message:
                st.session_state.auth_view = "Login"
                st.session_state.login_email = email.strip()
                st.session_state.registration_notice = "This email is already registered. Please log in instead."
                st.rerun()
            if "username already taken" in lower_message:
                st.warning("That username is already taken. Log in if it belongs to you, or choose another username.")
                return
            st.error(message)


if debug_auth_enabled():
    info_banner("Debugger mode active. Login and registration are disabled.", "info")
    user = get("/auth/me")
    if user:
        st.sidebar.success(f"Debug user @{user['user_name']}")
        metric_grid(
            [
                {"label": "Current Cash", "value": format_inr(user["current_cash"], compact=True)},
                {"label": "Starting Cash", "value": format_inr(user["starting_cash"], compact=True)},
                {"label": "Risk Profile", "value": user["risk_profile"].title()},
            ],
            columns=3,
        )
        st.write(f"Name: {user['name']}")
        st.write(f"Public username: @{user['user_name']}")
        info_banner(
            "Use the pages in the sidebar to explore markets, view data ingestion stats, "
            "manage portfolios, paper trades, strategies, and backtests.",
            "info",
        )
    else:
        st.warning("Debugger mode is enabled, but the backend could not return the debug user.")
elif st.session_state.token:
    user = get("/auth/me")
    if user:
        st.sidebar.success(f"Logged in as @{user['user_name']}")
        if st.sidebar.button("Logout"):
            post("/auth/logout")
            clear_auth_state()
            st.rerun()
        metric_grid(
            [
                {"label": "Current Cash", "value": format_inr(user["current_cash"], compact=True)},
                {"label": "Starting Cash", "value": format_inr(user["starting_cash"], compact=True)},
                {"label": "Risk Profile", "value": user["risk_profile"].title()},
            ],
            columns=3,
        )
        st.write(f"Name: {user['name']}")
        st.write(f"Public username: @{user['user_name']}")
        info_banner(
            "Use the pages in the sidebar to explore markets, view data ingestion stats, "
            "manage portfolios, paper trades, strategies, and backtests.",
            "info",
        )
    else:
        clear_auth_state()
        st.rerun()
else:
    auth_options = ["Login", "Register"]
    auth_view = st.radio(
        "Authentication",
        auth_options,
        index=auth_options.index(st.session_state.auth_view),
        horizontal=True,
        label_visibility="collapsed",
    )
    st.session_state.auth_view = auth_view
    if auth_view == "Login":
        login_form()
    else:
        register_form()

log_page_load("Home", PAGE_STARTED_AT)
