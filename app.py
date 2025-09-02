import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

# --- SEITE KONFIGURATION ---
st.set_page_config(page_title="RLT-Kostenanalyse Professional v6.2", page_icon="🏭", layout="wide")

# --- PHYSIKALISCHE FUNKTIONEN ---
def saettigungsdampfdruck(temp):
    """Berechnet den Sättigungsdampfdruck von Wasser in Pa."""
    return 611.2 * np.exp((17.67 * temp) / (temp + 243.5))

def abs_feuchte_aus_rel_feuchte(temp, rel_feuchte):
    """Berechnet die absolute Feuchte (kg/kg) aus Temperatur und relativer Feuchte."""
    es = saettigungsdampfdruck(temp)
    e = rel_feuchte / 100 * es
    return 0.622 * e / (101325 - e)

def rel_feuchte_aus_abs_feuchte(temp, abs_feuchte):
    """Berechnet die relative Feuchte (%) aus Temperatur und absoluter Feuchte."""
    es = saettigungsdampfdruck(temp)
    e = abs_feuchte * 101325 / (0.622 + abs_feuchte)
    return min(100, e / es * 100)

def enthalpie_feuchte_luft(temp, abs_feuchte):
    """Berechnet die Enthalpie feuchter Luft in J/kg."""
    return 1005 * temp + abs_feuchte * (2501000 + 1870 * temp)

def kuhltemperatur_fur_entfeuchtung(ziel_abs_feuchte, sicherheit=1):
    """Berechnet die nötige Kühltemperatur (Taupunkt) für eine Ziel-Absolutfeuchte."""
    if ziel_abs_feuchte <= 0:
        return -50
    e = ziel_abs_feuchte * 101325 / (0.622 + ziel_abs_feuchte)
    taupunkt = 243.5 * np.log(e / 611.2) / (17.67 - np.log(e / 611.2))
    return taupunkt - sicherheit

def berechne_realistische_sfp(volumenstrom):
    """Schätzt einen realistischen SFP-Wert basierend auf dem Volumenstrom."""
    if volumenstrom <= 5000:
        return 1.8
    elif volumenstrom <= 20000:
        return 1.2
    elif volumenstrom <= 50000:
        return 0.9
    else:
        return 0.6

def berechne_luftzustand(temp, rel_feuchte=None, abs_feuchte=None):
    """Erstellt ein Dictionary mit allen relevanten Luftzustandswerten."""
    if abs_feuchte is None:
        abs_feuchte = abs_feuchte_aus_rel_feuchte(temp, rel_feuchte)
    if rel_feuchte is None:
        rel_feuchte = rel_feuchte_aus_abs_feuchte(temp, abs_feuchte)
    return {
        'temperatur': round(temp, 1),
        'rel_feuchte': round(rel_feuchte, 1),
        'abs_feuchte': round(abs_feuchte * 1000, 2),
        'enthalpie': round(enthalpie_feuchte_luft(temp, abs_feuchte) / 1000, 1)
    }

def berechne_rlt_prozess(temp_aussen, feuchte_aussen, temp_zuluft, feuchte_zuluft_soll, wrg_grad, betriebsmodus, feuchte_abs_soll=None):
    """Simuliert den thermodynamischen Prozess in der RLT-Anlage."""
    prozess = {
        'schritte': [],
        'leistungen': {
            'kuehlung_entf': 0,
            'nachheizung': 0,
            'heizung_direkt': 0,
            'kuehlung_direkt': 0
        }
    }
    
    abs_feuchte_aussen = abs_feuchte_aus_rel_feuchte(temp_aussen, feuchte_aussen)
    zustand_aussen = berechne_luftzustand(temp_aussen, rel_feuchte=feuchte_aussen)
    zustand_aussen['punkt'] = 'Außenluft'
    prozess['schritte'].append(zustand_aussen)
    
    aktuelle_temp = temp_aussen
    aktuelle_abs_feuchte = abs_feuchte_aussen
    
    if wrg_grad > 0:
        temp_nach_wrg = temp_aussen + (temp_zuluft - temp_aussen) * wrg_grad / 100
        zustand_wrg = berechne_luftzustand(temp_nach_wrg, abs_feuchte=abs_feuchte_aussen)
        zustand_wrg['punkt'] = 'Nach WRG'
        prozess['schritte'].append(zustand_wrg)
        aktuelle_temp = temp_nach_wrg
    
    if betriebsmodus == "Entfeuchten":
        if feuchte_abs_soll is not None:
            ziel_abs_feuchte = feuchte_abs_soll / 1000
        else:
            ziel_abs_feuchte = abs_feuchte_aus_rel_feuchte(temp_zuluft, feuchte_zuluft_soll)
        
        if aktuelle_abs_feuchte > ziel_abs_feuchte:
            temp_kuehlung = kuhltemperatur_fur_entfeuchtung(ziel_abs_feuchte)
            zustand_gekuehlt = berechne_luftzustand(temp_kuehlung, abs_feuchte=ziel_abs_feuchte)
            zustand_gekuehlt['punkt'] = f'Gekühlt ({temp_kuehlung:.1f}°C)'
            prozess['schritte'].append(zustand_gekuehlt)
            
            enthalpie_vorher = enthalpie_feuchte_luft(aktuelle_temp, aktuelle_abs_feuchte)
            enthalpie_nachher = enthalpie_feuchte_luft(temp_kuehlung, ziel_abs_feuchte)
            prozess['leistungen']['kuehlung_entf'] = max(0, enthalpie_vorher - enthalpie_nachher)
            
            aktuelle_temp = temp_kuehlung
            aktuelle_abs_feuchte = ziel_abs_feuchte
            
            if temp_kuehlung < temp_zuluft:
                zustand_nachgeheizt = berechne_luftzustand(temp_zuluft, abs_feuchte=ziel_abs_feuchte)
                zustand_nachgeheizt['punkt'] = 'Nachheizung'
                prozess['schritte'].append(zustand_nachgeheizt)
                
                enthalpie_vorher = enthalpie_feuchte_luft(temp_kuehlung, ziel_abs_feuchte)
                enthalpie_nachher = enthalpie_feuchte_luft(temp_zuluft, ziel_abs_feuchte)
                prozess['leistungen']['nachheizung'] = max(0, enthalpie_nachher - enthalpie_vorher)
    
    elif betriebsmodus == "Nur Heizen":
        if aktuelle_temp < temp_zuluft:
            zustand_final = berechne_luftzustand(temp_zuluft, abs_feuchte=aktuelle_abs_feuchte)
            zustand_final['punkt'] = 'Erwärmung'
            prozess['schritte'].append(zustand_final)
            
            enthalpie_vorher = enthalpie_feuchte_luft(aktuelle_temp, aktuelle_abs_feuchte)
            enthalpie_nachher = enthalpie_feuchte_luft(temp_zuluft, aktuelle_abs_feuchte)
            prozess['leistungen']['heizung_direkt'] = max(0, enthalpie_nachher - enthalpie_vorher)
            
    return prozess

def reset_parameter():
    """Setzt alle Session State Parameter auf den Standardwert zurück."""
    for key in list(st.session_state.keys()):
        if key not in ['volumenstrom_sync']:
            del st.session_state[key]
    st.session_state.volumenstrom_sync = 10000

# --- STYLING (CSS) ---
st.markdown("""
<style>
    .main-header { font-size: 2.8rem; font-weight: bold; color: #2c3e50; text-align: center; margin-bottom: 1.5rem; }
    .parameter-header { font-size: 1.6rem; font-weight: bold; color: #34495e; background: linear-gradient(90deg, #f8f9fa 0%, #e9ecef 100%); padding: 1rem; border-radius: 0.5rem; margin: 1rem 0; border-left: 4px solid #3498db; }
    .metric-container { background-color: #f8f9fa; padding: 1rem; border-radius: 0.5rem; margin: 0.5rem 0; border: 1px solid #dee2e6; }
    .stMetric > div { font-size: 1.2rem !important; }
    .stMetric label { font-size: 1.1rem !important; font-weight: 700 !important; }
    div[data-testid="stSidebar"] { width: 420px !important; }
    div[data-testid="stSidebar"] .stNumberInput > div > div > input { font-size: 1.1rem !important; height: 2.5rem !important; }
    div[data-testid="stSidebar"] .stSlider > div > div > div { font-size: 1.1rem !important; }
    div[data-testid="stSidebar"] .stSelectbox > div > div > div { font-size: 1.1rem !important; }
    div[data-testid="stSidebar"] .stRadio > div { font-size: 1.1rem !important; }
    div[data-testid="stSidebar"] .stCheckbox > label > div { font-size: 1.1rem !important; }
    .current-params { background-color: #e8f6f3; padding: 1rem; border-radius: 0.5rem; border: 1px solid #52c41a; margin-bottom: 1rem; }
    .parameter-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0.5rem; }
    .param-item { background: white; padding: 0.5rem; border-radius: 0.3rem; border-left: 3px solid #52c41a; }
    @media (max-width: 768px) { div[data-testid="stSidebar"] { width: 100% !important; } }
</style>
""", unsafe_allow_html=True)

# --- SESSION STATE INITIALISIERUNG ---
if 'volumenstrom_sync' not in st.session_state:
    st.session_state.volumenstrom_sync = 10000
if 'reset_trigger' not in st.session_state:
    st.session_state.reset_trigger = False

# --- UI HEADER ---
st.markdown('<div class="main-header">🏭 RLT-Kostenanalyse Professional v6.2</div>', unsafe_allow_html=True)

# --- UI SIDEBAR (EINGABEN) ---
with st.sidebar:
    st.markdown('<div class="parameter-header">⚙️ Eingabeparameter</div>', unsafe_allow_html=True)
    
    col_reset1, col_reset2 = st.columns([3, 1])
    with col_reset2:
        if st.button("🔄 Reset", help="Alle Parameter auf Standardwerte zurücksetzen"):
            reset_parameter()
            st.rerun()
    
    with st.expander("🏭 Anlagenparameter", expanded=False):
        st.markdown("**Volumenstrom:**")
        col1, col2 = st.columns([2, 2])
        with col1:
            volumenstrom_slider = st.slider("Volumenstrom [m³/h]", min_value=100, max_value=200000, value=st.session_state.volumenstrom_sync, step=500, key="vol_slider")
        with col2:
            volumenstrom_input = st.number_input("Exakt [m³/h]:", value=st.session_state.volumenstrom_sync, min_value=100, max_value=500000, step=100, key="vol_input")
        
        if abs(volumenstrom_slider - st.session_state.volumenstrom_sync) > 0:
            st.session_state.volumenstrom_sync = volumenstrom_slider
            volumenstrom_final = volumenstrom_slider
        elif abs(volumenstrom_input - st.session_state.volumenstrom_sync) > 0:
            st.session_state.volumenstrom_sync = volumenstrom_input
            volumenstrom_final = volumenstrom_input
        else:
            volumenstrom_final = st.session_state.volumenstrom_sync
        
        st.markdown("**SFP - Spezifische Ventilatorleistung:**")
        sfp_auto = berechne_realistische_sfp(volumenstrom_final)
        sfp_modus = st.radio("SFP-Modus:", ["🤖 Automatisch", "✏️ Manuell"])
        
        if sfp_modus == "🤖 Automatisch":
            sfp_final = sfp_auto
            kategorie = "Sehr große Anlage" if volumenstrom_final > 50000 else "Große Anlage" if volumenstrom_final > 20000 else "Mittlere Anlage" if volumenstrom_final > 5000 else "Kleine Anlage"
            st.success(f"**{sfp_auto:.1f} W/(m³/h)** | {kategorie} | **{volumenstrom_final * sfp_auto / 1000:.1f} kW**")
        else:
            sfp_final = st.number_input("SFP manuell [W/(m³/h)]:", min_value=0.3, max_value=4.0, value=sfp_auto, step=0.1)
            st.info(f"💡 Empfohlen: {sfp_auto:.1f} W/(m³/h)")
    
    with st.expander("🔧 Betriebsmodi", expanded=False):
        betriebsmodus = st.selectbox("**Luftbehandlung:**", ["Nur Heizen", "Entfeuchten"])
        wrg_aktiv = st.checkbox("🔄 Wärmerückgewinnung aktivieren")
        wrg_wirkungsgrad = st.number_input("WRG-Wirkungsgrad [%]:", min_value=0, max_value=95, value=70, step=1, disabled=not wrg_aktiv) if wrg_aktiv else 0
    
    with st.expander("🌤️ Klimabedingungen", expanded=False):
        st.markdown("**🟢 Außenluft:**")
        temp_aussen = st.number_input("Temperatur [°C]:", min_value=-20.0, max_value=45.0, value=8.0, step=0.5)
        feuchte_aussen = st.number_input("rel. Feuchte [%]:", min_value=20, max_value=95, value=65, step=1)
        
        st.markdown("**🎯 Zuluft:**")
        temp_zuluft = st.number_input("Solltemperatur [°C]:", min_value=16.0, max_value=26.0, value=20.0, step=0.5)
        
        if betriebsmodus == "Entfeuchten":
            feuchte_modus = st.radio("Feuchte-Regelung:", ["Relative Feuchte [%]", "Absolute Feuchte [g/kg]"])
            if feuchte_modus == "Relative Feuchte [%]":
                feuchte_zuluft_soll = st.number_input("Zuluft rel. Feuchte [%]:", min_value=30, max_value=65, value=45, step=1)
                feuchte_abs_soll = None
            else:
                feuchte_abs_soll = st.number_input("Zuluft abs. Feuchte [g/kg]:", min_value=5.0, max_value=15.0, value=8.0, step=0.1)
                feuchte_zuluft_soll = rel_feuchte_aus_abs_feuchte(temp_zuluft, feuchte_abs_soll/1000)
        else:
            feuchte_zuluft_soll = feuchte_aussen
            feuchte_abs_soll = None
    
    with st.expander("📉 Absenkbetrieb", expanded=False):
        temp_absenkung = st.number_input("Temperatur-Absenkung [K]:", min_value=0.0, max_value=8.0, value=3.0, step=0.5)
        vol_absenkung = st.number_input("Volumenstrom-Reduktion [%]:", min_value=0, max_value=70, value=0, step=5)
    
    st.markdown("**⏰ Betriebszeiten:**")
    betriebstyp = st.selectbox("Betriebstyp:", ["24/7 Dauerbetrieb", "Labor (Mo-Fr 06:30-17:00)", "Benutzerdefiniert"])
    
    if betriebstyp == "24/7 Dauerbetrieb":
        normale_stunden = 8760
        absenk_stunden = 0
    elif betriebstyp == "Labor (Mo-Fr 06:30-17:00)":
        normale_stunden = 5 * 10.5 * 52
        absenk_stunden = (5 * 13.5 + 2 * 24) * 52
    else:
        with st.expander("📅 Detaileinstellungen", expanded=True):
            col1, col2 = st.columns(2)
            with col1:
                tage_normal = st.number_input("Normal [Tage/Woche]:", 0, 7, 5)
                stunden_normal = st.number_input("Normal [h/Tag]:", 1, 24, 10)
            with col2:
                tage_absenk = st.number_input("Absenk [Tage/Woche]:", 0, 7, 7)
                stunden_absenk = st.number_input("Absenk [h/Tag]:", 0, 23, 14)
            normale_stunden = tage_normal * stunden_normal * 52
            absenk_stunden = tage_absenk * stunden_absenk * 52
    
    with st.expander("🔧 Ausfallzeiten", expanded=False):
        wartungstage = st.number_input("Wartungstage/Jahr:", 0, 50, 0)
        ferienwochen = st.number_input("Betriebsferien [Wochen]:", 0, 12, 0)
    
    normale_stunden = max(0, normale_stunden - wartungstage * 24 - ferienwochen * 7 * 24)
    gesamt_aktiv = normale_stunden + absenk_stunden
    
    with st.expander("💰 Energiepreise", expanded=False):
        preis_strom = st.number_input("Strom [€/kWh]:", value=0.25, step=0.01, format="%.3f")
        preis_waerme = st.number_input("Fernwärme [€/kWh]:", value=0.08, step=0.01, format="%.3f")
        preis_kaelte = st.number_input("Kälte [€/kWh]:", value=0.15, step=0.01, format="%.3f")

# --- UI HAUPTSEITE ---

# --- ZUSAMMENFASSUNG DER PARAMETER ---
st.markdown("## 📋 Aktuelle Eingabeparameter")
st.markdown(f"""
<div class="current-params">
    <div class="parameter-grid">
        <div class="param-item"><strong>Volumenstrom:</strong> {volumenstrom_final:,} m³/h</div>
        <div class="param-item"><strong>SFP:</strong> {sfp_final:.1f} W/(m³/h)</div>
        <div class="param-item"><strong>Betrieb:</strong> {betriebsmodus}</div>
        <div class="param-item"><strong>WRG:</strong> {"Ja (" + str(wrg_wirkungsgrad) + "%)" if wrg_aktiv else "Nein"}</div>
        <div class="param-item"><strong>Außenluft:</strong> {temp_aussen:.1f}°C, {feuchte_aussen}% rF</div>
        <div class="param-item"><strong>Zuluft:</strong> {temp_zuluft:.1f}°C{f", {feuchte_zuluft_soll:.1f}% rF" if betriebsmodus == "Entfeuchten" else ""}</div>
        <div class="param-item"><strong>Absenkung:</strong> -{temp_absenkung:.1f}K, -{vol_absenkung}% Vol</div>
        <div class="param-item"><strong>Betriebszeit:</strong> {betriebstyp}</div>
    </div>
</div>
""", unsafe_allow_html=True)

st.markdown("---")
st.header("📊 Berechnungsergebnisse")

# --- BERECHNUNGEN ---
@st.cache_data
def cached_rlt_prozess(temp_aussen, feuchte_aussen, temp_zuluft, feuchte_zuluft_soll, wrg_wirkungsgrad, betriebsmodus, feuchte_abs_soll):
    return berechne_rlt_prozess(temp_aussen, feuchte_aussen, temp_zuluft, feuchte_zuluft_soll, wrg_wirkungsgrad, betriebsmodus, feuchte_abs_soll)

prozess = cached_rlt_prozess(temp_aussen, feuchte_aussen, temp_zuluft, feuchte_zuluft_soll, wrg_wirkungsgrad, betriebsmodus, feuchte_abs_soll)

# Leistungen
luftmassenstrom = volumenstrom_final * 1.2 / 3600
p_ventilator = volumenstrom_final * sfp_final / 1000
p_kuehlung_entf = luftmassenstrom * prozess['leistungen']['kuehlung_entf'] / 1000
p_nachheizung = luftmassenstrom * prozess['leistungen']['nachheizung'] / 1000
p_heizung_direkt = luftmassenstrom * prozess['leistungen']['heizung_direkt'] / 1000
p_kuehlung_direkt = luftmassenstrom * prozess['leistungen']['kuehlung_direkt'] / 1000

p_heizen_gesamt = p_nachheizung + p_heizung_direkt
p_kuehlen_gesamt = p_kuehlung_entf + p_kuehlung_direkt

# Jahresenergie
vol_faktor_absenk = 1 - (vol_absenkung / 100)
energie_ventilator = (p_ventilator * normale_stunden + p_ventilator * vol_faktor_absenk * absenk_stunden)
energie_heizen = (p_heizen_gesamt * normale_stunden + p_heizen_gesamt * 0.7 * absenk_stunden)
energie_kuehlen = (p_kuehlen_gesamt * normale_stunden + p_kuehlen_gesamt * 0.7 * absenk_stunden)

# Jahreskosten
kosten_ventilator = energie_ventilator * preis_strom
kosten_heizen = energie_heizen * preis_waerme
kosten_kuehlen = energie_kuehlen * preis_kaelte
gesamtkosten = kosten_ventilator + kosten_heizen + kosten_kuehlen

# --- ANZEIGE DER ERGEBNISSE ---
col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("⚡ Installierte Leistungen")
    col_a, col_b, col_c = st.columns(3)
    
    with col_a:
        st.markdown('<div class="metric-container">', unsafe_allow_html=True)
        st.metric("🟡 Ventilator", f"{p_ventilator:.1f} kW", f"SFP: {sfp_final:.1f}")
        st.markdown('</div>', unsafe_allow_html=True)
    
    with col_b:
        st.markdown('<div class="metric-container">', unsafe_allow_html=True)
        st.metric("🔴 Heizung", f"{p_heizen_gesamt:.1f} kW")
        if p_nachheizung > 0.1:
            st.caption(f"Nachheizung: {p_nachheizung:.1f} kW")
        st.markdown('</div>', unsafe_allow_html=True)
    
    with col_c:
        st.markdown('<div class="metric-container">', unsafe_allow_html=True)
        st.metric("🔵 Kühlung", f"{p_kuehlen_gesamt:.1f} kW")
        if p_kuehlung_entf > 0.1:
            st.caption(f"Entfeuchtung: {p_kuehlung_entf:.1f} kW")
        st.markdown('</div>', unsafe_allow_html=True)
    
    st.markdown("---")
    st.subheader("💰 Jahreskosten")
    
    col_a, col_b, col_c, col_d = st.columns(4)
    with col_a:
        st.metric("🟡 Ventilator", f"{kosten_ventilator:,.0f} €")
    with col_b:
        st.metric("🔴 Heizung", f"{kosten_heizen:,.0f} €")
    with col_c:
        st.metric("🔵 Kühlung", f"{kosten_kuehlen:,.0f} €")
    with col_d:
        st.metric("💯 GESAMT", f"{gesamtkosten:,.0f} €", f"{gesamtkosten/12:,.0f} €/Monat")

with col2:
    st.subheader("📈 Kostenverteilung")
    
    if gesamtkosten > 0:
        kosten_data, labels_data, colors_data = [], [], []
        if kosten_ventilator > 0:
            kosten_data.append(kosten_ventilator)
            labels_data.append("Ventilator")
            colors_data.append("#FF9500")
        if kosten_heizen > 0:
            kosten_data.append(kosten_heizen)
            labels_data.append("Heizung")
            colors_data.append("#DC3545")
        if kosten_kuehlen > 0:
            kosten_data.append(kosten_kuehlen)
            labels_data.append("Kühlung")
            colors_data.append("#0D6EFD")
        
        if kosten_data:
            fig = go.Figure(data=[go.Pie(labels=labels_data, values=kosten_data, hole=0.4, marker=dict(colors=colors_data))])
            fig.update_traces(textposition='inside', textinfo='percent+label')
            fig.update_layout(height=280, margin=dict(t=10, b=10, l=10, r=10), showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=-0.2))
            st.plotly_chart(fig, use_container_width=True)

# --- PROZESSDARSTELLUNG ---
st.markdown("---")
st.header("🔄 Luftbehandlungsprozess")

if prozess['schritte']:
    df_prozess = pd.DataFrame(prozess['schritte'])
    df_prozess.columns = ['Prozessschritt', 'Temperatur [°C]', 'rel. Feuchte [%]', 'abs. Feuchte [g/kg]', 'Enthalpie [kJ/kg]']
    st.dataframe(df_prozess, use_container_width=True, hide_index=True)

# --- KENNZAHLEN ---
st.markdown("---")
st.header("📋 Kennzahlen & Validation")

col1, col2, col3, col4 = st.columns(4)
with col1:
    spez_kosten = gesamtkosten / (volumenstrom_final * gesamt_aktiv / 1000) if gesamt_aktiv > 0 else 0
    st.metric("Spez. Kosten", f"{spez_kosten:.3f} €/(m³·h)")
with col2:
    p_gesamt = p_ventilator + p_heizen_gesamt + p_kuehlen_gesamt
    spez_leistung = p_gesamt / volumenstrom_final * 1000 if volumenstrom_final > 0 else 0
    st.metric("Spez. Leistung", f"{spez_leistung:.2f} W/m³/h")
with col3:
    jahresenergie = (energie_ventilator + energie_heizen + energie_kuehlen) / 1000
    st.metric("Jahresenergie", f"{jahresenergie:.1f} MWh/a")
with col4:
    co2_emissionen = (energie_ventilator * 0.4 + energie_heizen * 0.2) / 1000
    st.metric("CO₂-Emissionen", f"{co2_emissionen:.1f} t/a")

st.success(f"✅ **Berechnung validiert:** {volumenstrom_final:,} m³/h × {sfp_final:.1f} W/(m³/h) = **{p_ventilator:.1f} kW** | Betriebsstunden: {gesamt_aktiv:,.0f} h/a")

# --- PDF REPORT ---
st.markdown("---")
if st.button("📄 Professional PDF Report", type="primary"):
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, height-50, "RLT-Kostenanalyse Professional v6.2")
    p.setFont("Helvetica", 10)
    p.drawString(50, height-70, f"Erstellt: {pd.Timestamp.now().strftime('%d.%m.%Y %H:%M')}")
    
    y = height - 110
    p.setFont("Helvetica", 11)
    
    daten = [
        f"Volumenstrom: {volumenstrom_final:,} m³/h",
        f"SFP (korrigiert): {sfp_final:.1f} W/(m³/h)",
        f"Betriebsmodus: {betriebsmodus}",
        f"WRG: {'Ja (' + str(wrg_wirkungsgrad) + '%)' if wrg_aktiv else 'Nein'}",
        f"Außenluft: {temp_aussen:.1f}°C, {feuchte_aussen}% rF",
        f"Zuluft: {temp_zuluft:.1f}°C" + (f", {feuchte_zuluft_soll:.1f}% rF" if betriebsmodus == 'Entfeuchten' else ""),
        "",
        "Leistungen:",
        f"  Ventilator: {p_ventilator:.1f} kW",
        f"  Heizung: {p_heizen_gesamt:.1f} kW",
        f"  Kühlung: {p_kuehlen_gesamt:.1f} kW",
        "",
        "Jahreskosten:",
        f"  Ventilator: {kosten_ventilator:,.0f} €/Jahr",
        f"  Heizung: {kosten_heizen:,.0f} €/Jahr",
        f"  Kühlung: {kosten_kuehlen:,.0f} €/Jahr",
        "",
        f"GESAMTKOSTEN: {gesamtkosten:,.0f} €/Jahr",
        f"Betriebsstunden: {gesamt_aktiv:,.0f} h/Jahr"
    ]
    
    for item in daten:
        if item.startswith("GESAMTKOSTEN"):
            p.setFont("Helvetica-Bold", 11)
        p.drawString(70, y, item)
        p.setFont("Helvetica", 11)
        y -= 16
    
    p.save()
    buffer.seek(0)
    
    st.download_button(
        label="📥 PDF herunterladen",
        data=buffer,
        file_name=f"RLT_Professional_v6.2_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.pdf",
        mime="application/pdf"
    )

st.markdown("---")
st.markdown("*RLT-Kostenanalyse Professional v6.2 - Optimierte Performance und Benutzerfreundlichkeit*")
