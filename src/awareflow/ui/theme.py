"""Shared presentation; business data stays in native Streamlit components."""
from pathlib import Path

import streamlit as st


ROOT = Path(__file__).resolve().parents[3]
PAGE_ICONS = {
    "ภาพรวม": "dashboard", "รอบอบรม": "calendar_month",
    "ตรวจเนื้อหา": "fact_check", "อีเมล": "mail",
    "ผู้ใช้และตั้งค่า": "manage_accounts", "ข้อสอบของฉัน": "menu_book",
}


def navigation_label(page):
    return f":material/{PAGE_ICONS[page]}:　{page}"


def apply_theme():
    st.markdown("""<style>
    .stMainBlockContainer {max-width:1180px;padding:3.5rem 3rem 4rem}
    h1,h2,h3 {letter-spacing:0;line-height:1.5!important}
    h1 {padding-bottom:.4rem!important}
    [data-testid="stCaptionContainer"] {color:#686B73;line-height:1.7}
    [data-testid="stText"] {font-family:"Noto Sans Thai",sans-serif;line-height:1.9;white-space:pre-wrap}
    [data-testid="stSidebar"] {border-right:1px solid #E2E3E6}
    [data-testid="stSidebarUserContent"] {padding:1.5rem 1.25rem}
    .af-brand {display:flex;align-items:center;gap:12px;padding:0 0 24px}
    .af-brand svg {width:38px;height:38px;flex-shrink:0}
    .af-brand-name {font-size:23px;font-weight:700;letter-spacing:-.6px;line-height:1.3}
    .af-brand-tag {font-size:9px;letter-spacing:.65px;color:#686B73;margin-top:3px;white-space:nowrap}
    [data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] {gap:4px}
    [data-testid="stSidebar"] [data-testid="stRadio"] label[data-baseweb="radio"] {
      border-radius:6px;padding:10px 12px;margin:0;width:100%;gap:0;border-left:3px solid transparent}
    [data-testid="stSidebar"] [data-testid="stRadio"] label[data-baseweb="radio"]>div:first-child {display:none}
    [data-testid="stSidebar"] [data-testid="stRadio"] label[data-baseweb="radio"]:has(input:checked) {
      background:#FCECEE;border-left-color:#CF000D;color:#A5000A}
    [data-testid="stSidebar"] [data-testid="stRadio"] label[data-baseweb="radio"]:hover {background:#EEEFF1}
    [data-testid="stSidebar"] [data-testid="stRadio"] label[data-baseweb="radio"]:focus-within {
      outline:2px solid #CF000D;outline-offset:2px}
    [data-testid="stSidebar"] [data-testid="stRadio"] p {font-size:14px;font-weight:500}
    [data-testid="stMetric"] {border:1px solid #E2E3E6;border-radius:6px;padding:18px 20px;background:var(--background-color)}
    [data-testid="stMetricLabel"] p {font-size:13px;color:#686B73}
    [data-testid="stMetricValue"] {font-size:30px;font-weight:600}
    [data-testid="stForm"] {border:1px solid #E2E3E6;padding:24px;border-radius:8px}
    [data-testid="stButton"] button,[data-testid="stDownloadButton"] button {min-height:42px;font-weight:500}
    [data-testid="stTabs"] [role="tablist"] {gap:24px;border-bottom:1px solid #E2E3E6}
    [data-testid="stTabs"] [role="tab"] {padding:12px 0}
    .st-key-page_learner_page {max-width:850px;margin:0 auto}
    .st-key-page_learner_page [data-testid="stText"] {font-size:17px;line-height:1.95}
    .st-key-page_learner_page h2 {margin-top:1.25rem}
    .st-key-page_learner_page h3 {padding-top:.75rem}
    .st-key-lesson_scenario {background:#F8F8F9;border-left:3px solid #CF000D;border-radius:4px;padding:20px 24px}
    @media(max-width:700px) {
      .stMainBlockContainer {padding:3rem 1.1rem}
      [data-testid="stForm"] {padding:16px}
      [data-testid="stMetric"] {padding:12px}
      .st-key-lesson_scenario {padding:16px}
    }
    </style>""", unsafe_allow_html=True)


def brand():
    # Static, repository-owned SVG; no model/user text is interpolated as HTML.
    mark = (ROOT / "static/awareflow-mark.svg").read_text()
    st.markdown(f'<div class="af-brand">{mark}<div><div class="af-brand-name">AwareFlow</div>'
                '<div class="af-brand-tag">SECURITY AWARENESS</div></div></div>', unsafe_allow_html=True)
