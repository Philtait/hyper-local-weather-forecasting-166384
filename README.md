# Imarika — Hyper-Localised Agricultural Weather Forecasting
**A Case Study of Busia County, Kenya**

Imarika is a farm-level weather forecasting system for smallholder farmers in Busia County, Western Kenya. It combines real-time data from 26 IoT weather stations with satellite reanalysis datasets to deliver 7-day forecasts, planting advisories, flood alerts, and SMS notifications.

---

## Dataset
| Source | Records | Period |
|---|---|---|
| IoT Stations (Anga) | 764,309 | Aug 2025 – present |
| Open-Meteo (ERA5) | 255,840 | Aug 2025 – present |
| NASA POWER | 63,674 | Jan 2020 – present |
| CHIRPS | 62,504 | Jan 2020 – present |

## Stack
**Data:** Python, MongoDB, PostgreSQL  
**Models:** SARIMA → LSTM → Microsoft Aurora (QLoRA)  
**Delivery:** FastAPI, React PWA, Africa's Talking SMS

## Progress
| Sprint | Focus | Status |
|---|---|---|
| 1 | Data Collection | Complete |
| 2 | Preprocessing | Complete |
| 3 | SARIMA & LSTM | Complete |
| 4 | Aurora Fine-tuning | Pending |
| 5 | API & Delivery | Pending |

---

**Student:** Philip Tait (166384) · Strathmore University · ICS 4E  
**Supervisor:** Ms. Julliet Kirui · **Industry:** @iLabAfrica Research and Innnovation Center Strathmore University