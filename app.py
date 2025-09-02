import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import time, timedelta

# --- SEITENKONFIGURATION ---
st.set_page_config(page_title="Interaktive RLT-Analyse Pro v8.3", page_icon="🌬️", layout="wide")

# --- KONSTANTEN UND FARBEN ---
LUFTDICHTE_KG_M3 = 1.2
HEATING_COLOR = "#D32F2F"
COOLING_COLOR = "#1976D2"
FAN_COLOR = "#F57C00"
TOTAL_COLOR = "#388E3C"

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
    return min(100, max(0, e / es * 100))

def enthalpie_feuchte_luft(temp, abs_feuchte):
    return 1.006 * temp + abs_feuchte * (2501 + 1.86 * temp)

def taupunkt(temp, rel_feuchte):
    e = rel_feuchte / 100 * saettigungsdampfdruck(temp)
    if e <= 0: return -273.15
    return 243.5 * np.log(e / 611.2) / (17.67 - np.log(e / 611.2))

def kuhltemperatur_fur_entfeuchtung(ziel_abs_feuchte, sicherheit=1):
    if ziel_abs_feuchte <= 0: return -50
    e = ziel_abs_feuchte * 101325 / (0.622 + ziel_abs_feuchte)
    tp = 243.5 * np.log(e / 611.2) / (17.67 - np.log(e / 611.2))
    return tp - sicherheit

def berechne_realistische_sfp(volumenstrom):
    if volumenstrom <= 5000: return 1.8
    elif volumenstrom <= 20000: return 1.2
    elif volumenstrom <= 50000: return 0.9
    else: return 0.6

def berechne_luftzustand(temp, rel_feuchte=None, abs_feuchte=None):
    if abs_feuchte is None: abs_feuchte = abs_feuchte_aus_rel_feuchte(temp, rel_feuchte)
    if rel_feuchte is None: rel_feuchte = rel_feuchte_aus_abs_feuchte(temp, abs_feuchte)
    return { 'T [°C]': temp, 'rF [%]': rel_feuchte, 'x [g/kg]': abs_feuchte * 1000, 'h [kJ/kg]': enthalpie_feuchte_luft(temp, abs_feuchte), 'Taupunkt [°C]': taupunkt(temp, rel_feuchte) }

# --- PROZESS-BERECHNUNG (Gecacht für Performance) ---
@st.cache_data
def berechne_rlt_prozess(params):
    prozess = {'schritte': [], 'leistungen': {'kuehlung_entf': 0, 'nachheizung_NE': 0, 'heizung_direkt_VE': 0}}
    
    abs_feuchte_aussen = abs_feuchte_aus_rel_feuchte(params['temp_aussen'], params['feuchte_aussen'])
    zustand1 = {'Punkt': '1: Außenluft', **berechne_luftzustand(params['temp_aussen'], abs_feuchte=abs_feuchte_aussen)}
    prozess['schritte'].append(zustand1)
    akt_temp, akt_abs_feuchte = zustand1['T [°C]'], abs_feuchte_aussen

    if params.get('wrg_wirkungsgrad', 0) > 0:
        temp_nach_wrg = akt_temp + (params['temp_abluft'] - akt_temp) * params['wrg_wirkungsgrad'] / 100
        zustand2 = {'Punkt': '2: Nach WRG', **berechne_luftzustand(temp_nach_wrg, abs_feuchte=akt_abs_feuchte)}
        prozess['schritte'].append(zustand2)
        akt_temp = zustand2['T [°C]']
    
    h_vor_behandlung = enthalpie_feuchte_luft(akt_temp, akt_abs_feuchte)

    if params['betriebsmodus'] == "Entfeuchten":
        ziel_abs_feuchte = abs_feuchte_aus_rel_feuchte(params['temp_zuluft'], params['feuchte_zuluft_soll'])
        if akt_abs_feuchte > ziel_abs_feuchte:
            temp_kuehlung = kuhltemperatur_fur_entfeuchtung(ziel_abs_feuchte)
            zustand3 = {'Punkt': '3: Nach Kühler', **berechne_luftzustand(temp_kuehlung, abs_feuchte=ziel_abs_feuchte)}
            prozess['schritte'].append(zustand3)
            prozess['leistungen']['kuehlung_entf'] = max(0, h_vor_behandlung - zustand3['h [kJ/kg]'])
            if temp_kuehlung < params['temp_zuluft']:
                zustand4 = {'Punkt': '4: Zuluft (NE)', **berechne_luftzustand(params['temp_zuluft'], abs_feuchte=ziel_abs_feuchte)}
                prozess['schritte'].append(zustand4)
                prozess['leistungen']['nachheizung_NE'] = max(0, zustand4['h [kJ/kg]'] - zustand3['h [kJ/kg]'])
    elif params['betriebsmodus'] == "Nur Heizen" and akt_temp < params['temp_zuluft']:
        zustand_final = {'Punkt': '4: Zuluft (VE)', **berechne_luftzustand(params['temp_zuluft'], abs_feuchte=akt_abs_feuchte)}
        prozess['schritte'].append(zustand_final)
        prozess['leistungen']['heizung_direkt_VE'] = max(0, zustand_final['h [kJ/kg]'] - h_vor_behandlung)
    
    return prozess

# --- h-x Diagramm Plot Funktion ---
def plot_hx_diagram(prozess_schritte):
    fig = go.Figure()
    t_range = np.linspace(-20, 40, 61)
    x_saettigung = [abs_feuchte_aus_rel_feuchte(t, 100) * 1000 for t in t_range]
    fig.add_trace(go.Scatter(x=x_saettigung, y=t_range, mode='lines', name='Sättigungslinie (100% rF)', line=dict(color='grey', width=2, dash='dash')))
    
    if prozess_schritte:
        temps = [p['T [°C]'] for p in prozess_schritte]
        feuchten = [p['x [g/kg]'] for p in prozess_schritte]
        labels = [p['Punkt'] for p in prozess_schritte]
        fig.add_trace(go.Scatter(x=feuchten, y=temps, mode='lines+markers+text', name='Prozess',
                                line=dict(color=HEATING_COLOR, width=3), marker=dict(size=10, color=COOLING_COLOR),
                                text=labels, textposition="top right", textfont=dict(size=12)))
    
    fig.update_layout(title="h-x Diagramm (Temperatur-Feuchte)", xaxis_title="Absolute Feuchte x [g/kg]", yaxis_title="Temperatur T [°C]", yaxis=dict(range=[-15, 35]), xaxis=dict(range=[0, 20]), legend=dict(x=0.01, y=0.99))
    return fig

# --- STYLING ---
st.markdown("""<style>...</style>""", unsafe_allow_html=True) # CSS bleibt gleich

# --- ZUSTANDS-MANAGEMENT (Session State) ---
def set_defaults():
    st.session_state.update({
        'volumenstrom': 10000, 'sfp_modus': "🤖 Automatisch", 'sfp_manuell': 1.2,
        'betriebsmodus': "Entfeuchten", 'wrg_aktiv': True, 'wrg_wirkungsgrad': 75,
        'temp_aussen': 5.0, 'feuchte_aussen': 85, 'temp_zuluft': 22.0, 'feuchte_zuluft_soll': 50, 'temp_abluft': 23.0,
        'profil1_aktiv': True, 'tage1': ["Mo", "Di", "Mi", "Do", "Fr"], 'start_zeit1': time(8, 0), 'end_zeit1': time(18, 0),
        'profil2_aktiv': False, 'tage2': ["Sa"], 'start_zeit2': time(9, 0), 'end_zeit2': time(14, 0),
        'absenk_aktiv': True, 'vol_reduktion_absenk': 40, 'heiz_reduktion_absenk': 60,
        'preis_strom': 0.25, 'preis_waerme': 0.12, 'preis_kaelte': 0.18
    })

if 'volumenstrom' not in st.session_state:
    set_defaults()

# --- UI SIDEBAR (EINGABEN) ---
with st.sidebar:
    st.title("🌬️ RLT-Analyse Pro")
    if st.button("🔄 Parameter zurücksetzen", type="primary"):
        set_defaults()
        st.rerun()

    st.markdown('<p class="sidebar-header">Anlagenparameter</p>', unsafe_allow_html=True)
    st.number_input("Volumenstrom [m³/h]", 100, 500000, key='volumenstrom', step=100)
    st.radio("SFP-Modus:", ["🤖 Automatisch", "✏️ Manuell"], key='sfp_modus', horizontal=True)
    if st.session_state.sfp_modus == "✏️ Manuell":
        st.number_input("SFP manuell [W/(m³/h)]:", 0.3, 4.0, key='sfp_manuell', step=0.1)

    st.markdown('<p class="sidebar-header">Betrieb & Prozess</p>', unsafe_allow_html=True)
    # VERBESSERT: selectbox zu radio geändert
    st.radio("Luftbehandlung:", ["Nur Heizen", "Entfeuchten"], key='betriebsmodus', horizontal=True)
    st.checkbox("🔄 Wärmerückgewinnung (WRG)", key='wrg_aktiv')
    if st.session_state.wrg_aktiv:
        st.slider("WRG-Wirkungsgrad [%]:", 0, 95, key='wrg_wirkungsgrad')

    st.markdown('<p class="sidebar-header">Klimabedingungen (Auslegungspunkt)</p>', unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    col1.number_input("Außenluft T [°C]:", -20.0, 45.0, key='temp_aussen', step=0.5)
    col2.number_input("Außenluft rF [%]:", 20, 100, key='feuchte_aussen', step=1)
    col1.number_input("Zuluft T [°C]:", 16.0, 26.0, key='temp_zuluft', step=0.5)
    if st.session_state.betriebsmodus == "Entfeuchten":
         col2.number_input("Zuluft rF [%]:", 30, 65, key='feuchte_zuluft_soll', step=1)
    st.number_input("Annahme Abluft T [°C]:", 18.0, 30.0, key='temp_abluft', help="Wird für die WRG-Berechnung benötigt.")
    
    # NEU: Flexiblere Betriebszeiten
    st.markdown('<p class="sidebar-header">Betriebszeiten</p>', unsafe_allow_html=True)
    st.checkbox("Profil 1 (z.B. Werktage)", key='profil1_aktiv')
    if st.session_state.profil1_aktiv:
        st.multiselect("Tage (Profil 1):", ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"], key='tage1')
        col1, col2 = st.columns(2)
        col1.time_input('Startzeit (Profil 1)', key='start_zeit1')
        col2.time_input('Endzeit (Profil 1)', key='end_zeit1')
    
    st.checkbox("Profil 2 (z.B. Wochenende)", key='profil2_aktiv')
    if st.session_state.profil2_aktiv:
        st.multiselect("Tage (Profil 2):", ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"], key='tage2')
        col1, col2 = st.columns(2)
        col1.time_input('Startzeit (Profil 2)', key='start_zeit2')
        col2.time_input('Endzeit (Profil 2)', key='end_zeit2')
        
    # NEU: Absenkbetrieb
    st.markdown('<p class="sidebar-header">Absenkbetrieb</p>', unsafe_allow_html=True)
    st.checkbox("Absenkbetrieb außerhalb der Nutzungszeit aktivieren", key='absenk_aktiv')
    if st.session_state.absenk_aktiv:
        st.slider("Reduktion Volumenstrom im Absenkbetrieb [%]", 0, 80, key='vol_reduktion_absenk', help="Reduziert den Luftvolumenstrom.")
        st.slider("Reduktion Heizleistung im Absenkbetrieb [%]", 0, 100, key='heiz_reduktion_absenk', help="Reduziert die Heizleistung (z.B. durch niedrigere Soll-Temperatur).")

    st.markdown('<p class="sidebar-header">Energiekosten</p>', unsafe_allow_html=True)
    # ... Energiekosten-Inputs bleiben gleich ...

# --- HAUPTBEREICH & BERECHNUNGEN ---
st.markdown('<div class="main-header">Interaktives Dashboard für RLT-Anlagen</div>', unsafe_allow_html=True)

# KORRIGIERT: Fehler bei deaktivierter WRG abfangen
prozess_params = {k: v for k, v in st.session_state.items()}
if not prozess_params.get('wrg_aktiv', False):
    prozess_params['wrg_wirkungsgrad'] = 0
prozess = berechne_rlt_prozess(prozess_params)

# ... weitere Berechnungen ...
sfp_final = berechne_realistische_sfp(st.session_state.volumenstrom) if st.session_state.sfp_modus == '🤖 Automatisch' else st.session_state.sfp_manuell
luftmassenstrom_kgs = st.session_state.volumenstrom * LUFTDICHTE_KG_M3 / 3600
p_ventilator = st.session_state.volumenstrom * sfp_final / 1000
p_kuehlung = luftmassenstrom_kgs * prozess['leistungen']['kuehlung_entf']
p_heizung_ve = luftmassenstrom_kgs * prozess['leistungen']['heizung_direkt_VE']
p_heizung_ne = luftmassenstrom_kgs * prozess['leistungen']['nachheizung_NE']
p_heizung_gesamt = p_heizung_ve + p_heizung_ne

# VERBESSERT: Jahresstunden-Berechnung mit Profilen
def berechne_stunden(start, end, tage):
    if not tage: return 0 # KORRIGIERT: Fehler bei leeren Tagen abfangen
    stunden_pro_tag = max(0, (end.hour - start.hour) + (end.minute - start.minute)/60)
    return stunden_pro_tag * len(tage) * 52

jahresstunden_normal = 0
if st.session_state.profil1_aktiv:
    jahresstunden_normal += berechne_stunden(st.session_state.start_zeit1, st.session_state.end_zeit1, st.session_state.tage1)
if st.session_state.profil2_aktiv:
    jahresstunden_normal += berechne_stunden(st.session_state.start_zeit2, st.session_state.end_zeit2, st.session_state.tage2)

# VERBESSERT: Jahreskosten mit Absenkbetrieb
jahresstunden_absenk = max(0, 8760 - jahresstunden_normal) if st.session_state.absenk_aktiv else 0

# Kosten Normalbetrieb
kosten_ventilator_normal = p_ventilator * jahresstunden_normal * st.session_state.preis_strom
kosten_heizung_normal = p_heizung_gesamt * jahresstunden_normal * st.session_state.preis_waerme
kosten_kuehlung_normal = p_kuehlung * jahresstunden_normal * st.session_state.preis_kaelte

# Kosten Absenkbetrieb (approximiert)
faktor_vol_absenk = (1 - st.session_state.get('vol_reduktion_absenk', 0) / 100)
p_ventilator_absenk = p_ventilator * (faktor_vol_absenk**2) # Leistung skaliert quadratisch mit Volumenstrom
faktor_heiz_absenk = (1 - st.session_state.get('heiz_reduktion_absenk', 0) / 100)
p_heizung_absenk = p_heizung_gesamt * faktor_heiz_absenk # Vereinfachte Annahme
kosten_ventilator_absenk = p_ventilator_absenk * jahresstunden_absenk * st.session_state.preis_strom
kosten_heizung_absenk = p_heizung_absenk * jahresstunden_absenk * st.session_state.preis_waerme

# Gesamtkosten
kosten_ventilator = kosten_ventilator_normal + kosten_ventilator_absenk
kosten_heizung = kosten_heizung_normal + kosten_heizung_absenk
kosten_kuehlung = kosten_kuehlung_normal # Annahme: Keine Kühlung im Absenkbetrieb
gesamtkosten = kosten_ventilator + kosten_heizung + kosten_kuehlung

# ... weitere Kennzahlen ...

# --- ERGEBNIS-ANZEIGE IN TABS ---
tab1, tab2, tab3 = st.tabs(["**📊 Dashboard**", "**📈 h-x Diagramm**", "**📋 Prozesstabelle**"])
with tab1:
    st.subheader("Leistung am Auslegungspunkt")
    col1, col2, col3 = st.columns(3)
    # ... Metriken für Leistung ...
    with col2:
        st.markdown(f'<div class="metric-container" style="border-left: 5px solid {HEATING_COLOR};">', unsafe_allow_html=True)
        st.metric(label="🔥 Heizleistung (Gesamt)", value=f"{p_heizung_gesamt:.1f} kW")
        st.caption(f"VE: {p_heizung_ve:.1f} kW | NE: {p_heizung_ne:.1f} kW")
        # VERBESSERT: Hinweis bei 0 kW
        if p_heizung_gesamt < 0.1 and st.session_state.betriebsmodus == "Nur Heizen":
            st.info("Kein Heizbedarf bei diesen Bedingungen.")
        st.markdown('</div>', unsafe_allow_html=True)
    # ...
    
    st.subheader(f"Geschätzte Jahreswerte")
    st.caption(f"Normalbetrieb: {jahresstunden_normal:,.0f} h/a | Absenkbetrieb: {jahresstunden_absenk:,.0f} h/a")
    # ... Metriken für Kosten ...

# ... Rest des Codes für Tabs 2 und 3 bleibt gleich ...
