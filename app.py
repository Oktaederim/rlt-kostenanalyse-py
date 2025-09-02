import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import time

# --- SEITENKONFIGURATION ---
st.set_page_config(page_title="Interaktive RLT-Analyse Pro v8.1", page_icon="🌬️", layout="wide")

# --- KONSTANTEN UND FARBEN ---
LUFTDICHTE_KG_M3 = 1.2
HEATING_COLOR = "#D32F2F"
COOLING_COLOR = "#1976D2"
FAN_COLOR = "#F57C00"
TOTAL_COLOR = "#388E3C"

# --- PHYSIKALISCHE FUNKTIONEN ---
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
    # Umstellung auf kJ/kg für konsistente Einheiten
    return 1.006 * temp + abs_feuchte * (2501 + 1.86 * temp)

def taupunkt(temp, rel_feuchte):
    e = rel_feuchte / 100 * saettigungsdampfdruck(temp)
    if e <= 0: return -273.15 # physikalisch nicht sinnvoll, aber verhindert Rechenfehler
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
    return {
        'T [°C]': temp,
        'rF [%]': rel_feuchte,
        'x [g/kg]': abs_feuchte * 1000,
        'h [kJ/kg]': enthalpie_feuchte_luft(temp, abs_feuchte),
        'Taupunkt [°C]': taupunkt(temp, rel_feuchte)
    }

# --- PROZESS-BERECHNUNG (Gecacht für Performance) ---
@st.cache_data
def berechne_rlt_prozess(params):
    prozess = {'schritte': [], 'leistungen': {'kuehlung_entf': 0, 'nachheizung_NE': 0, 'heizung_direkt_VE': 0}}
    
    # Zustand 1: Außenluft
    abs_feuchte_aussen = abs_feuchte_aus_rel_feuchte(params['temp_aussen'], params['feuchte_aussen'])
    zustand1 = {'Punkt': '1: Außenluft', **berechne_luftzustand(params['temp_aussen'], abs_feuchte=abs_feuchte_aussen)}
    prozess['schritte'].append(zustand1)
    akt_temp, akt_abs_feuchte = zustand1['T [°C]'], abs_feuchte_aussen

    # Zustand 2: Nach WRG
    if params['wrg_wirkungsgrad'] > 0:
        temp_nach_wrg = akt_temp + (params['temp_abluft'] - akt_temp) * params['wrg_wirkungsgrad'] / 100
        zustand2 = {'Punkt': '2: Nach WRG', **berechne_luftzustand(temp_nach_wrg, abs_feuchte=akt_abs_feuchte)}
        prozess['schritte'].append(zustand2)
        akt_temp = zustand2['T [°C]']
    
    h_vor_behandlung = enthalpie_feuchte_luft(akt_temp, akt_abs_feuchte)

    # Luftbehandlungsprozess
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
    
    temps = [p['T [°C]'] for p in prozess_schritte]
    feuchten = [p['x [g/kg]'] for p in prozess_schritte]
    labels = [p['Punkt'] for p in prozess_schritte]
    fig.add_trace(go.Scatter(x=feuchten, y=temps, mode='lines+markers+text', name='Prozess',
                             line=dict(color=HEATING_COLOR, width=3), marker=dict(size=10, color=COOLING_COLOR),
                             text=labels, textposition="top right", textfont=dict(size=12)))
    
    fig.update_layout(title="h-x Diagramm (Temperatur-Feuchte)",
                      xaxis_title="Absolute Feuchte x [g/kg]",
                      yaxis_title="Temperatur T [°C]",
                      yaxis=dict(range=[-15, 35]),
                      xaxis=dict(range=[0, 20]),
                      legend=dict(x=0.01, y=0.99))
    return fig

# --- STYLING ---
st.markdown("""
<style>
    .main-header { font-size: 2.5rem; font-weight: bold; color: #2c3e50; text-align: center; margin-bottom: 1rem; }
    .sidebar-header { font-size: 1.5rem; font-weight: bold; color: #34495e; padding-bottom: 0.5rem; margin-top:1rem; border-bottom: 2px solid #3498db; }
    .metric-container { background-color: #FFFFFF; padding: 1rem; border-radius: 0.5rem; border: 1px solid #dee2e6; box-shadow: 0 1px 3px rgba(0,0,0,0.12); }
    .stMetric { text-align: center; }
    [data-testid="stMetricValue"] { font-size: 2.2rem; font-weight: 600; }
    [data-testid="stMetricLabel"] { font-size: 1.1rem; font-weight: 500; }
    .stButton>button { width: 100%; }
</style>
""", unsafe_allow_html=True)

# --- ZUSTANDS-MANAGEMENT (Session State) ---
def set_defaults():
    st.session_state.volumenstrom = 10000
    st.session_state.sfp_modus = "🤖 Automatisch"
    st.session_state.sfp_manuell = 1.2
    st.session_state.betriebsmodus = "Entfeuchten"
    st.session_state.wrg_aktiv = True
    st.session_state.wrg_wirkungsgrad = 75
    st.session_state.temp_aussen = 5.0
    st.session_state.feuchte_aussen = 85
    st.session_state.temp_zuluft = 22.0
    st.session_state.feuchte_zuluft_soll = 50
    st.session_state.temp_abluft = 23.0
    st.session_state.tage = ["Mo", "Di", "Mi", "Do", "Fr"]
    st.session_state.start_zeit = time(8, 0)
    st.session_state.end_zeit = time(18, 0)
    st.session_state.preis_strom = 0.25
    st.session_state.preis_waerme = 0.12
    st.session_state.preis_kaelte = 0.18

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
    st.selectbox("Luftbehandlung:", ["Nur Heizen", "Entfeuchten"], key='betriebsmodus')
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
    
    st.markdown('<p class="sidebar-header">Betriebszeiten</p>', unsafe_allow_html=True)
    st.multiselect("Betriebstage:", ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"], key='tage')
    col1, col2 = st.columns(2)
    col1.time_input('Startzeit', key='start_zeit')
    col2.time_input('Endzeit', key='end_zeit')

    st.markdown('<p class="sidebar-header">Energiekosten</p>', unsafe_allow_html=True)
    col1, col2, col3 = st.columns(3)
    col1.number_input("Strom [€/kWh]:", 0.0, 1.0, key='preis_strom', step=0.01, format="%.2f")
    col2.number_input("Wärme [€/kWh]:", 0.0, 1.0, key='preis_waerme', step=0.01, format="%.2f")
    col3.number_input("Kälte [€/kWh]:", 0.0, 1.0, key='preis_kaelte', step=0.01, format="%.2f")

# --- HAUPTBEREICH ---
st.markdown('<div class="main-header">Interaktives Dashboard für RLT-Anlagen</div>', unsafe_allow_html=True)

# --- BERECHNUNGEN (live bei jeder Interaktion) ---
sfp_final = berechne_realistische_sfp(st.session_state.volumenstrom) if st.session_state.sfp_modus == '🤖 Automatisch' else st.session_state.sfp_manuell
prozess_params = {k: v for k, v in st.session_state.items()}
prozess = berechne_rlt_prozess(prozess_params)

luftmassenstrom_kgs = st.session_state.volumenstrom * LUFTDICHTE_KG_M3 / 3600
p_ventilator = st.session_state.volumenstrom * sfp_final / 1000
p_kuehlung = luftmassenstrom_kgs * prozess['leistungen']['kuehlung_entf']
p_heizung_ve = luftmassenstrom_kgs * prozess['leistungen']['heizung_direkt_VE']
p_heizung_ne = luftmassenstrom_kgs * prozess['leistungen']['nachheizung_NE']
p_heizung_gesamt = p_heizung_ve + p_heizung_ne

stunden_pro_tag = (st.session_state.end_zeit.hour - st.session_state.start_zeit.hour) + (st.session_state.end_zeit.minute - st.session_state.start_zeit.minute)/60
jahresstunden = stunden_pro_tag * len(st.session_state.tage) * 52

kosten_ventilator = p_ventilator * jahresstunden * st.session_state.preis_strom
kosten_heizung = p_heizung_gesamt * jahresstunden * st.session_state.preis_waerme
kosten_kuehlung = p_kuehlung * jahresstunden * st.session_state.preis_kaelte
gesamtkosten = kosten_ventilator + kosten_heizung + kosten_kuehlung

zuluft_zustand = prozess['schritte'][-1] if prozess['schritte'] else prozess['schritte'][0]
wasserausfall_kgh = luftmassenstrom_kgs * max(0, prozess['schritte'][0]['x [g/kg]'] - zuluft_zustand['x [g/kg]']) * 3.6

# --- ERGEBNIS-ANZEIGE IN TABS ---
tab1, tab2, tab3 = st.tabs(["**📊 Dashboard**", "**📈 h-x Diagramm**", "**📋 Prozesstabelle**"])
with tab1:
    st.subheader("Leistung am Auslegungspunkt")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(f'<div class="metric-container" style="border-left: 5px solid {FAN_COLOR};">', unsafe_allow_html=True)
        st.metric(label="💨 Ventilatorleistung", value=f"{p_ventilator:.1f} kW", delta=f"{sfp_final:.2f} W/(m³/h)", delta_color="off")
        st.markdown('</div>', unsafe_allow_html=True)
    with col2:
        st.markdown(f'<div class="metric-container" style="border-left: 5px solid {HEATING_COLOR};">', unsafe_allow_html=True)
        st.metric(label="🔥 Heizleistung (Gesamt)", value=f"{p_heizung_gesamt:.1f} kW")
        st.caption(f"VE: {p_heizung_ve:.1f} kW | NE: {p_heizung_ne:.1f} kW")
        st.markdown('</div>', unsafe_allow_html=True)
    with col3:
        st.markdown(f'<div class="metric-container" style="border-left: 5px solid {COOLING_COLOR};">', unsafe_allow_html=True)
        st.metric(label="❄️ Kühlleistung", value=f"{p_kuehlung:.1f} kW")
        # KORRIGIERT: Tippfehler in 'wasserausfall_kgh' behoben
        if wasserausfall_kgh > 0.1:
            st.caption(f"💧 Kondensat: {wasserausfall_kgh:.1f} kg/h")
        st.markdown('</div>', unsafe_allow_html=True)

    st.subheader(f"Geschätzte Jahreswerte (Basis: {jahresstunden:,.0f} h/a)")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f'<div class="metric-container">', unsafe_allow_html=True)
        st.metric(label="Ventilator-Kosten", value=f"{kosten_ventilator:,.0f} €")
        st.markdown('</div>', unsafe_allow_html=True)
    with col2:
        st.markdown(f'<div class="metric-container">', unsafe_allow_html=True)
        st.metric(label="Heizkosten", value=f"{kosten_heizen:,.0f} €")
        st.markdown('</div>', unsafe_allow_html=True)
    with col3:
        st.markdown(f'<div class="metric-container">', unsafe_allow_html=True)
        st.metric(label="Kühlkosten", value=f"{kosten_kuehlen:,.0f} €")
        st.markdown('</div>', unsafe_allow_html=True)
    with col4:
        st.markdown(f'<div class="metric-container" style="border: 2px solid {TOTAL_COLOR};">', unsafe_allow_html=True)
        st.metric(label="Gesamtkosten p.a.", value=f"{gesamtkosten:,.0f} €")
        st.markdown('</div>', unsafe_allow_html=True)
        
with tab2:
    st.plotly_chart(plot_hx_diagram(prozess['schritte']), use_container_width=True)

with tab3:
    st.subheader("Zustandsgrößen der Luft an jedem Prozesspunkt")
    if prozess['schritte']:
        df_prozess = pd.DataFrame(prozess['schritte'])
        df_prozess_display = df_prozess.style.format({
            'T [°C]': '{:.1f}', 'rF [%]': '{:.1f}', 'x [g/kg]': '{:.2f}',
            'h [kJ/kg]': '{:.1f}', 'Taupunkt [°C]': '{:.1f}'
        })
        st.dataframe(df_prozess_display, use_container_width=True, hide_index=True)
    else:
        st.warning("Für die aktuellen Einstellungen findet kein relevanter Luftbehandlungsprozess statt.")
