"""Visual style for the dashboard: navy header, white rounded cards, large text.

inject_style() adds the CSS once per page run. The palette and card shapes
follow the project UI mockups; status colours are always paired with words.
"""

import html

import streamlit as st

NAVY = "#0F2C5C"
INK = "#14213D"
MUTED = "#4A5568"
LINE = "#D7DEE8"
PAGE = "#F3F6FA"

CSS = f"""
<style>
  html, body, [class*="css"], .stMarkdown, .stText, p, li, label {{ font-size: 17px; color: {INK}; }}
  .block-container {{ max-width: 1280px; padding-top: 1.6rem; }}
  [data-testid="stAppViewContainer"] {{ background: {PAGE}; }}
  h1, h2, h3 {{ color: {NAVY}; }}
  .im-header {{ background: {NAVY}; color: #FFFFFF; border-radius: 14px; padding: 18px 22px; margin-bottom: 14px; }}
  .im-header h1 {{ color: #FFFFFF; font-size: 1.9rem; margin: 0 0 4px 0; padding: 0; }}
  .im-header p {{ color: #DCE6F5; margin: 0; font-size: 1.05rem; }}
  .im-summary {{ background: #FFFFFF; border: 1px solid {LINE}; border-radius: 14px; padding: 16px 20px;
                 font-size: 1.25rem; font-weight: 600; color: {INK}; margin-bottom: 14px; }}
  .im-card {{ background: #FFFFFF; border: 1px solid {LINE}; border-radius: 14px; padding: 18px 20px;
              margin-bottom: 14px; }}
  .im-card.accent {{ border-left-width: 8px; }}
  .im-label {{ text-transform: uppercase; letter-spacing: .06em; font-size: .85rem; font-weight: 700;
               color: {MUTED}; margin-bottom: 6px; }}
  .im-headline {{ font-size: 1.7rem; font-weight: 800; margin: 2px 0 4px 0; line-height: 1.25; }}
  .im-station {{ font-size: 1.1rem; color: {MUTED}; font-weight: 600; }}
  .im-big {{ font-size: 1.2rem; font-weight: 600; line-height: 1.5; }}
  .im-days {{ display: flex; gap: 8px; flex-wrap: wrap; }}
  .im-day {{ flex: 1 1 88px; min-width: 88px; background: #FFFFFF; border: 1px solid {LINE};
             border-radius: 12px; padding: 10px 8px; text-align: center; }}
  .im-day b {{ display: block; font-size: 1.05rem; color: {NAVY}; }}
  .im-day span {{ display: block; font-size: 1rem; }}
  .im-day.alert {{ border: 2px solid; }}
  .im-now {{ display: flex; gap: 10px; flex-wrap: wrap; }}
  .im-now div {{ flex: 1 1 120px; background: #F7F9FC; border: 1px solid {LINE}; border-radius: 12px;
                 padding: 10px 12px; }}
  .im-now b {{ display: block; font-size: 1.35rem; color: {INK}; }}
  .im-sms {{ font-family: ui-monospace, Consolas, monospace; font-size: 1.05rem; background: #F7F9FC;
             border: 1px solid {LINE}; border-radius: 10px; padding: 12px 14px; }}
  .im-credit {{ font-size: .9rem; color: {MUTED}; }}
  .im-preview-banner {{ background: #FFE08A; color: #3A2A00; font-weight: 800; text-align: center;
                        border-radius: 10px; padding: 10px; margin-bottom: 12px; letter-spacing: .04em; }}
  .stButton button, .stSelectbox div[data-baseweb="select"] {{ min-height: 48px; font-size: 1.05rem; }}
  [data-testid="stMetric"] {{ background: #FFFFFF; border: 1px solid {LINE}; border-radius: 12px; padding: 12px; }}
  .architecture {{ display: flex; align-items: stretch; gap: 10px; margin: 16px 0; flex-wrap: wrap; }}
  .stage {{ flex: 1; min-width: 145px; padding: 18px 14px; border: 1px solid {LINE}; border-radius: 12px;
            background: #FFFFFF; }}
  .stage b {{ display: block; color: {NAVY}; margin-bottom: 10px; }}
  .stage span {{ font-size: 0.95rem; line-height: 1.6; color: {MUTED}; }}
  .arrow {{ align-self: center; color: {NAVY}; font-size: 1.4rem; }}
  @media (max-width: 800px) {{
    .im-headline {{ font-size: 1.4rem; }}
    .im-header h1 {{ font-size: 1.5rem; }}
  }}
</style>
"""


def inject_style():
    """Add the stylesheet once per page run."""
    st.markdown(CSS, unsafe_allow_html=True)


def text(value):
    return html.escape(str(value))


def header(title, subtitle):
    st.markdown(f'<div class="im-header"><h1>{text(title)}</h1><p>{text(subtitle)}</p></div>',
                unsafe_allow_html=True)


def card(body_html, colour=None):
    style = f' style="border-left-color:{colour}"' if colour else ""
    accent = " accent" if colour else ""
    st.markdown(f'<div class="im-card{accent}"{style}>{body_html}</div>', unsafe_allow_html=True)
