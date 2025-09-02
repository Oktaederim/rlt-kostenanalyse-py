import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
import datetime

# --- KONSTANTEN ---
# VERBESSERT: Konstanten zentral definieren für bessere Wartbarkeit
LUFTDICHTE_KG_M3 = 1.2
CO2_FAKTOR_STROM_KG_KWH = 0.4  # Beispielwert für Deutschland-Strommix
CO2_FAKTOR_WAERME_KG_KWH = 0.2  # Beispielwert für Fernwärme/Gas

# --- SEITE KONFIGURATION ---
st.set_page_config(page_title="RLT-Kostenanalyse Pro v7.0", page_icon="💨", layout="wide")


# --- PHYSIKALISCHE FUNKTIONEN (unverändert) ---
def saettigungsdampfdruck(temp):
    return 611.2 * np.exp((17.67 * temp) / (temp + 243.5))

def abs_feuchte_aus_rel_feuchte(temp, rel_feuchte):
    es = saettigungsdampfdruck(temp)
    e = rel_feuchte / 100 * es
    return 0.622 * e / (101325 - e)

def rel_feuchte_aus_abs_feuchte(temp, abs_feuchte):
    es = saettigungsdampfdruck(temp)
    e = abs_feuchte * 101325 / (0.622 + abs_feuchte)
    return min(100, e / es * 100)

def enthalpie_feuchte_luft(temp, abs_feuchte):
    return 1005 * temp + abs_feuchte * (2501000 + 1870 * temp)

def kuhltemperatur_fur_entfeuchtung(ziel_abs_feuchte, sicherheit=1):
    if ziel_abs_feuchte <= 0: return -50
    e = ziel_abs_feuchte * 101325 / (0.622 + ziel_abs_feuchte)
    taupunkt = 243.5 * np.log(e / 611.2) / (17.67 - np.log(e / 611.2))
    return taupunkt - sicherheit

def berechne_realistische_sfp(volumenstrom):
    if volumenstrom <= 5000: return 1.8
    elif volumenstrom <= 20000: return 1.2
    elif volumenstrom <= 50000: return 0.9
    else: return 0.6

def berechne_luftzustand(temp, rel_feuchte=None, abs_feuchte=None):
    if abs_feuchte is None: abs_feuchte = abs_feuchte_aus_rel_feuchte(temp, rel_feuchte)
    if rel_feuchte is None: rel_feuchte = rel_feuchte_aus_abs_feuchte(temp, abs_feuchte)
    return {
        'temperatur': round(temp, 1),
        'rel_feuchte': round(rel_feuchte, 1),
        'abs_feuchte': round(abs_feuchte * 1000, 2),
        'enthalpie': round(enthalpie_feuchte_luft(temp, abs_feuchte) / 1000, 1)
    }

# --- PROZESS- UND BERECHNUNGSFUNKTIONEN ---
@st.cache_data
def berechne_rlt_prozess(temp_aussen, feuchte_aussen, temp_zuluft, feuchte_zuluft_soll, wrg_grad, betriebsmodus, feuchte_abs_soll=None):
    # Diese Funktion ist rechenintensiv und bleibt gecacht
    prozess = {
        'schritte': [],
        'leistungen': {'kuehlung_entf': 0, 'nachheizung': 0, 'heizung_direkt': 0, 'kuehlung_direkt': 0}
    }
    abs_feuchte_aussen = abs_feuchte_aus_rel_feuchte(temp_aussen, feuchte_aussen)
    zustand_aussen = berechne_luftzustand(temp_aussen, rel_feuchte=feuchte_aussen)
    zustand_aussen['punkt'] = 'Außenluft'
    prozess['schritte'].append(zustand_aussen)
    aktuelle_temp, aktuelle_abs_feuchte = temp_aussen, abs_feuchte_aussen
    
    if wrg_grad > 0:
        temp_nach_wrg = temp_aussen + (temp_zuluft - temp_aussen) * wrg_grad / 100
        zustand_wrg = berechne_luftzustand(temp_nach_wrg, abs_feuchte=abs_feuchte_aussen)
        zustand_wrg['punkt'] = 'Nach WRG'
        prozess['schritte'].append(zustand_wrg)
        aktuelle_temp = temp_nach_wrg
    
    if betriebsmodus == "Entfeuchten":
        ziel_abs_feuchte = feuchte_abs_soll / 1000 if feuchte_abs_soll is not None else abs_feuchte_aus_rel_feuchte(temp_zuluft, feuchte_zuluft_soll)
        if aktuelle_abs_feuchte > ziel_abs_feuchte:
            temp_kuehlung = kuhltemperatur_fur_entfeuchtung(ziel_abs_feuchte)
            prozess['schritte'].append({'punkt': f'Gekühlt ({temp_kuehlung:.1f}°C)', **berechne_luftzustand(temp_kuehlung, abs_feuchte=ziel_abs_feuchte)})
            prozess['leistungen']['kuehlung_entf'] = max(0, enthalpie_feuchte_luft(aktuelle_temp, aktuelle_abs_feuchte) - enthalpie_feuchte_luft(temp_kuehlung, ziel_abs_feuchte))
            aktuelle_temp, aktuelle_abs_feuchte = temp_kuehlung, ziel_abs_feuchte
            if temp_kuehlung < temp_zuluft:
                prozess['schritte'].append({'punkt': 'Nachheizung', **berechne_luftzustand(temp_zuluft, abs_feuchte=ziel_abs_feuchte)})
                prozess['leistungen']['nachheizung'] = max(0, enthalpie_feuchte_luft(temp_zuluft, ziel_abs_feuchte) - enthalpie_feuchte_luft(temp_kuehlung, ziel_abs_feuchte))
    elif betriebsmodus == "Nur Heizen" and aktuelle_temp < temp_zuluft:
        prozess['schritte'].append({'punkt': 'Erwärmung', **berechne_luftzustand(temp_zuluft, abs_feuchte=aktuelle_abs_feuchte)})
        prozess['leistungen']['heizung_direkt'] = max(0, enthalpie_feuchte_luft(temp_zuluft, aktuelle_abs_feuchte) - enthalpie_feuchte_luft(aktuelle_temp, aktuelle_abs_feuchte))
    return prozess

# --- STYLING ---
st.markdown("""
<style>
    .main-header { font-size: 2.8rem; font-weight: bold; color: #2c3e50; text-align: center; margin-bottom: 1rem; }
    .parameter-header { font-size: 1.6rem; font-weight: bold; color: #34495e; padding-bottom: 0.5rem; border-bottom: 2px solid #3498db; }
    .metric-container { background-color: #f8f9fa; padding: 1rem; border-radius: 0.5rem; border: 1px solid #dee2e6; }
    .stButton>button { width: 100%; font-size: 1.2rem; height: 3rem; background-color: #28a745; color: white;}
</style>
""", unsafe_allow_html=True)


# --- UI LAYOUT ---
st.markdown('<div class="main-header">💨 RLT-Kostenanalyse Pro v7.0</div>', unsafe_allow_html=True)
st.markdown("Geben Sie links alle Anlagen- und Betriebsparameter ein und klicken Sie auf 'Berechnen', um die Ergebnisse zu sehen.")

# --- UI SIDEBAR (EINGABEN) ---
# VERBESSERT: Alle Eingaben in st.form für flüssige Bedienung
with st.sidebar:
    st.markdown('<div class="parameter-header">⚙️ Eingabeparameter</div>', unsafe_allow_html=True)
    
    with st.form(key="eingabe_form"):
        # VERBESSERT: Icons für bessere Übersichtlichkeit
        with st.expander("🏭 Anlagenparameter", expanded=True, icon="🏭"):
            # VERBESSERT: Vereinfachte Eingabe für Volumenstrom
            volumenstrom_final = st.number_input("Volumenstrom [m³/h]", min_value=100, max_value=500000, value=10000, step=100)
            
            sfp_auto = berechne_realistische_sfp(volumenstrom_final)
            sfp_modus = st.radio("SFP-Modus:", ["🤖 Automatisch", "✏️ Manuell"], horizontal=True)
            
            if sfp_modus == "🤖 Automatisch":
                sfp_final = sfp_auto
                st.info(f"Automatischer SFP: **{sfp_auto:.2f} W/(m³/h)**")
            else:
                sfp_final = st.number_input("SFP manuell [W/(m³/h)]:", min_value=0.3, max_value=4.0, value=sfp_auto, step=0.1)
        
        with st.expander("🔧 Betriebsmodi & WRG", expanded=True, icon="🔧"):
            betriebsmodus = st.selectbox("Luftbehandlung:", ["Nur Heizen", "Entfeuchten"])
            wrg_aktiv = st.checkbox("🔄 Wärmerückgewinnung aktivieren", value=True)
            wrg_wirkungsgrad = st.slider("WRG-Wirkungsgrad [%]:", 0, 95, 70, 1, disabled=not wrg_aktiv) if wrg_aktiv else 0
        
        with st.expander("🌤️ Klimabedingungen", expanded=True, icon="🌤️"):
            st.markdown("**Außenluft:**")
            col1, col2 = st.columns(2)
            temp_aussen = col1.number_input("Temperatur [°C]:", -20.0, 45.0, 5.0, 0.5)
            feuchte_aussen = col2.number_input("rel. Feuchte [%]:", 20, 95, 80, 1)
            
            st.markdown("**Zuluft (Sollwerte):**")
            col1, col2 = st.columns(2)
            temp_zuluft = col1.number_input("Temperatur [°C]:", 16.0, 26.0, 21.0, 0.5)
            
            feuchte_abs_soll, feuchte_zuluft_soll = None, feuchte_aussen
            if betriebsmodus == "Entfeuchten":
                feuchte_modus = col2.radio("Feuchte-Regelung:", ["rel. [%]", "abs. [g/kg]"], horizontal=True)
                if feuchte_modus == "rel. [%]":
                    feuchte_zuluft_soll = col2.number_input("rel. Feuchte [%]:", 30, 65, 50, 1)
                else:
                    feuchte_abs_soll = col2.number_input("abs. Feuchte [g/kg]:", 5.0, 15.0, 8.0, 0.1)
                    feuchte_zuluft_soll = rel_feuchte_aus_abs_feuchte(temp_zuluft, feuchte_abs_soll/1000)

        with st.expander("⏰ Betriebs- & Ausfallzeiten", expanded=False, icon="⏰"):
            betriebstyp = st.selectbox("Betriebsprofil:", ["24/7 Dauerbetrieb", "Büro (Mo-Fr 8-18 Uhr)", "Benutzerdefiniert"])
            if betriebstyp == "24/7 Dauerbetrieb":
                normale_stunden_basis = 8760
            elif betriebstyp == "Büro (Mo-Fr 8-18 Uhr)":
                normale_stunden_basis = 5 * 10 * 52
            else:
                stunden_pro_tag = st.slider("Stunden pro Tag:", 1, 24, 10)
                tage_pro_woche = st.slider("Tage pro Woche:", 1, 7, 5)
                normale_stunden_basis = stunden_pro_tag * tage_pro_woche * 52
            
            st.info(f"Grundbetriebszeit: {normale_stunden_basis:,.0f} Stunden/Jahr")
            wartungstage = st.number_input("Wartungstage/Jahr:", 0, 50, 2)
            normale_stunden = max(0, normale_stunden_basis - wartungstage * (normale_stunden_basis/365))
        
        with st.expander("💰 Energiepreise", expanded=False, icon="💰"):
            preis_strom = st.number_input("Strom [€/kWh]:", value=0.25, step=0.01, format="%.3f")
            preis_waerme = st.number_input("Wärme [€/kWh]:", value=0.12, step=0.01, format="%.3f")
            preis_kaelte = st.number_input("Kälte [€/kWh]:", value=0.18, step=0.01, format="%.3f")
            
        # NEU: Der Submit-Button löst die Berechnung aus
        submitted = st.form_submit_button(label="🚀 Berechnung starten")

# --- BERECHNUNG & ANZEIGE (wird nur nach Klick ausgeführt) ---
if submitted:
    # 1. Thermodynamischen Prozess berechnen
    prozess = berechne_rlt_prozess(temp_aussen, feuchte_aussen, temp_zuluft, feuchte_zuluft_soll, wrg_wirkungsgrad, betriebsmodus, feuchte_abs_soll)
    
    # 2. Leistungen berechnen
    luftmassenstrom = volumenstrom_final * LUFTDICHTE_KG_M3 / 3600
    p_ventilator = volumenstrom_final * sfp_final / 1000
    p_kuehlung_entf = luftmassenstrom * prozess['leistungen']['kuehlung_entf'] / 1000
    p_nachheizung = luftmassenstrom * prozess['leistungen']['nachheizung'] / 1000
    p_heizung_direkt = luftmassenstrom * prozess['leistungen']['heizung_direkt'] / 1000
    p_heizen_gesamt = p_nachheizung + p_heizung_direkt
    p_kuehlen_gesamt = p_kuehlung_entf

    # 3. Jahresenergie und -kosten berechnen
    # HINWEIS: Annahme, dass die berechnete Leistung für alle Betriebsstunden gilt. 
    # Für eine genauere Berechnung wären Klimadaten über das Jahr verteilt nötig.
    energie_ventilator = p_ventilator * normale_stunden
    energie_heizen = p_heizen_gesamt * normale_stunden
    energie_kuehlen = p_kuehlen_gesamt * normale_stunden
    
    kosten_ventilator = energie_ventilator * preis_strom
    kosten_heizen = energie_heizen * preis_waerme
    kosten_kuehlen = energie_kuehlen * preis_kaelte
    gesamtkosten = kosten_ventilator + kosten_heizen + kosten_kuehlen

    # --- ANZEIGE DER ERGEBNISSE ---
    st.header("📊 Berechnungsergebnisse")
    
    st.subheader("⚡ Installierte Leistungen")
    col1, col2, col3 = st.columns(3)
    col1.metric("Ventilator", f"{p_ventilator:.1f} kW", f"{sfp_final:.2f} W/(m³/h)")
    col2.metric("Heizregister", f"{p_heizen_gesamt:.1f} kW")
    col3.metric("Kühlregister", f"{p_kuehlen_gesamt:.1f} kW")
    
    st.subheader("💰 Geschätzte Jahreskosten")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Ventilator", f"{kosten_ventilator:,.0f} €")
    col2.metric("Heizung", f"{kosten_heizen:,.0f} €")
    col3.metric("Kühlung", f"{kosten_kuehlen:,.0f} €")
    with col4:
        st.metric("Gesamtkosten", f"{gesamtkosten:,.0f} €", f"{gesamtkosten/12:,.0f} €/Monat")

    st.markdown("---")
    
    col1, col2 = st.columns([1, 1])
    with col1:
        st.subheader("🔄 Luftbehandlungsprozess")
        if prozess['schritte']:
            df_prozess = pd.DataFrame(prozess['schritte'])
            df_prozess = df_prozess.rename(columns={
                'punkt': 'Prozessschritt', 'temperatur': 'T [°C]', 
                'rel_feuchte': 'rF [%]', 'abs_feuchte': 'x [g/kg]', 'enthalpie': 'h [kJ/kg]'
            })
            st.dataframe(df_prozess, use_container_width=True, hide_index=True)
            
    with col2:
        st.subheader("📈 Kostenverteilung")
        if gesamtkosten > 0:
            labels = ['Ventilator', 'Heizung', 'Kühlung']
            values = [kosten_ventilator, kosten_heizen, kosten_kuehlen]
            colors = ['#FF9500', '#DC3545', '#0D6EFD']
            fig = go.Figure(data=[go.Pie(labels=labels, values=values, hole=0.4, marker=dict(colors=colors))])
            fig.update_traces(textinfo='percent+label', textfont_size=14)
            fig.update_layout(height=300, margin=dict(t=10, b=10, l=10, r=10), showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")
    st.subheader("📋 Weitere Kennzahlen")
    co2_emissionen = (energie_ventilator * CO2_FAKTOR_STROM_KG_KWH + energie_heizen * CO2_FAKTOR_WAERME_KG_KWH) / 1000
    col1, col2, col3 = st.columns(3)
    jahresenergie = (energie_ventilator + energie_heizen + energie_kuehlen) / 1000
    col1.metric("Jahresenergiebedarf", f"{jahresenergie:.1f} MWh/a")
    col2.metric("CO₂-Emissionen (geschätzt)", f"{co2_emissionen:.1f} t/a")
    spez_kosten = gesamtkosten / volumenstrom_final if volumenstrom_final > 0 else 0
    col3.metric("Spez. Kosten pro m³/h", f"{spez_kosten:.2f} €/a")

else:
    st.info("⬅️ Bitte geben Sie links Ihre Parameter ein und starten Sie die Berechnung.")
