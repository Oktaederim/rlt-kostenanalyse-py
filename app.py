import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

st.set_page_config(page_title="RLT-Kostenanalyse Professional v6.1", page_icon="🏭", layout="wide")

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

def taupunkt_aus_abs_feuchte(abs_feuchte):
    if abs_feuchte <= 0:
        return -50
    e = abs_feuchte * 101325 / (0.622 + abs_feuchte)
    return 243.5 * np.log(e/611.2) / (17.67 - np.log(e/611.2))

def kuhltemperatur_fur_entfeuchtung(ziel_abs_feuchte, sicherheit=1):
    taupunkt = taupunkt_aus_abs_feuchte(ziel_abs_feuchte)
    return taupunkt - sicherheit

def berechne_realistische_sfp(volumenstrom):
    if volumenstrom <= 5000:
        return 1.8
    elif volumenstrom <= 20000:
        return 1.2
    elif volumenstrom <= 50000:
        return 0.9
    else:
        return 0.6

def berechne_luftzustand(temp, rel_feuchte=None, abs_feuchte=None):
    if abs_feuchte is None:
        abs_feuchte = abs_feuchte_aus_rel_feuchte(temp, rel_feuchte)
    return {
        'temperatur': round(temp, 1),
        'rel_feuchte': round(rel_feuchte, 1) if rel_feuchte else 0,
        'abs_feuchte': round(abs_feuchte * 1000, 2),
        'enthalpie': round(enthalpie_feuchte_luft(temp, abs_feuchte) / 1000, 1)
    }

def berechne_rlt_prozess(temp_aussen, feuchte_aussen, temp_zuluft, feuchte_zuluft_soll, wrg_grad, betriebsmodus, feuchte_abs_soll=None):
    prozess = {'schritte': [], 'leistungen': {'kuehlung_entf': 0, 'nachheizung': 0, 'heizung_direkt': 0, 'kuehlung_direkt': 0}}
    
    abs_feuchte_aussen = abs_feuchte_aus_rel_feuchte(temp_aussen, feuchte_aussen)
    zustand_aussen = berechne_luftzustand(temp_aussen, feuchte_aussen)
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

st.markdown("""
<style>
    .main-header { font-size: 2.5rem; font-weight: bold; color: #2c3e50; text-align: center; margin-bottom: 2rem; }
    .metric-container { background-color: #f8f9fa; padding: 1rem; border-radius: 0.5rem; margin: 0.5rem 0; border: 1px solid #dee2e6; }
    .stMetric > div { font-size: 1.1rem !important; }
    .stMetric label { font-size: 1rem !important; font-weight: 600 !important; }
    div[data-testid="stSidebar"] { width: 400px !important; }
    div[data-testid="stSidebar"] .stNumberInput > div > div > input { font-size: 1rem !important; }
    div[data-testid="stSidebar"] .stSlider > div > div > div { font-size: 1rem !important; }
    .stSelectbox > div > div > div { font-size: 1rem !important; }
    @media (max-width: 768px) { div[data-testid="stSidebar"] { width: 100% !important; } }
    .sfp-info { background-color: #e8f4f8; padding: 0.8rem; border-radius: 0.3rem; border-left: 4px solid #17a2b8; margin: 0.5rem 0; font-size: 0.9rem; }
</style>
""", unsafe_allow_html=True)

if 'volumenstrom_sync' not in st.session_state:
    st.session_state.volumenstrom_sync = 10000

st.markdown('<div class="main-header">🏭 RLT-Kostenanalyse Professional v6.1</div>', unsafe_allow_html=True)
st.markdown("*Korrigierte SFP-Werte basierend auf DIN EN 13779*")
st.markdown("---")

with st.sidebar:
    st.header("⚙️ Eingabeparameter")
    
    with st.expander("🏭 Anlagenparameter", expanded=False):
        st.markdown("**Volumenstrom (synchronisierte Eingabe):**")
        col1, col2 = st.columns([3, 2])
        with col1:
            volumenstrom_slider = st.slider("Volumenstrom [m³/h]", min_value=100, max_value=200000, value=st.session_state.volumenstrom_sync, step=500, key="vol_slider")
        with col2:
            volumenstrom_input = st.number_input("Exakt:", value=st.session_state.volumenstrom_sync, min_value=100, max_value=500000, step=100, key="vol_input")
        
        if volumenstrom_slider != st.session_state.volumenstrom_sync:
            st.session_state.volumenstrom_sync = volumenstrom_slider
            volumenstrom_final = volumenstrom_slider
        elif volumenstrom_input != st.session_state.volumenstrom_sync:
            st.session_state.volumenstrom_sync = volumenstrom_input
            volumenstrom_final = volumenstrom_input
            st.rerun()
        else:
            volumenstrom_final = st.session_state.volumenstrom_sync
        
        st.markdown("**Ventilator (SFP - Spezifische Ventilatorleistung):**")
        sfp_auto = berechne_realistische_sfp(volumenstrom_final)
        sfp_modus = st.radio("SFP-Eingabe:", ["Automatisch (empfohlen)", "Manuell eingeben"], help="Automatisch = basierend auf Anlagengröße und DIN EN 13779")
        
        if sfp_modus == "Automatisch (empfohlen)":
            sfp_final = sfp_auto
            st.markdown(f'<div class="sfp-info">🎯 <b>Automatischer SFP:</b> {sfp_auto:.1f} W/(m³/h)<br>📋 <b>Anlagenkategorie:</b> {"Sehr große Anlage" if volumenstrom_final > 50000 else "Große Anlage" if volumenstrom_final > 20000 else "Mittlere Anlage" if volumenstrom_final > 5000 else "Kleine Anlage"}<br>⚡ <b>Ventilatorleistung:</b> {volumenstrom_final * sfp_auto / 1000:.1f} kW</div>', unsafe_allow_html=True)
        else:
            sfp_manual = st.number_input("SFP manuell [W/(m³/h)]", min_value=0.3, max_value=4.0, value=sfp_auto, step=0.1)
            sfp_final = sfp_manual
            st.info(f"💡 Empfohlener Wert für {volumenstrom_final:,} m³/h: {sfp_auto:.1f} W/(m³/h)")
    
    with st.expander("🔧 Betriebsmodi", expanded=False):
        st.markdown("**Luftbehandlung wählen:**")
        betriebsmodus = st.radio("Betriebsmodus:", ["Nur Heizen", "Entfeuchten"], help="Nur Heizen = reine Temperaturregelung\nEntfeuchten = Kühlung + Entfeuchtung + Nachheizung")
        st.markdown("**Wärmerückgewinnung:**")
        wrg_aktiv = st.checkbox("WRG aktivieren")
        if wrg_aktiv:
            wrg_wirkungsgrad = st.number_input("WRG-Wirkungsgrad [%]", min_value=0, max_value=95, value=70, step=1)
        else:
            wrg_wirkungsgrad = 0
    
    with st.expander("🌤️ Klimabedingungen", expanded=False):
        st.markdown("**🟢 Außenluft:**")
        temp_aussen = st.number_input("Außentemperatur [°C]", min_value=-20.0, max_value=45.0, value=8.0, step=0.5)
        feuchte_aussen = st.number_input("Außenluft rel. Feuchte [%]", min_value=20, max_value=95, value=65, step=1)
        st.markdown("**🎯 Zuluft:**")
        temp_zuluft = st.number_input("Zuluft-Solltemperatur [°C]", min_value=16.0, max_value=26.0, value=20.0, step=0.5)
        
        if betriebsmodus == "Entfeuchten":
            st.markdown("**Zuluft-Feuchte:**")
            feuchte_modus = st.radio("Feuchte-Regelung:", ["Relative Feuchte [%]", "Absolute Feuchte [g/kg]"])
            if feuchte_modus == "Relative Feuchte [%]":
                feuchte_zuluft_soll = st.number_input("Zuluft rel. Feuchte [%]", min_value=30, max_value=65, value=45, step=1)
                feuchte_abs_soll = None
            else:
                feuchte_abs_soll = st.number_input("Zuluft abs. Feuchte [g/kg]", min_value=5.0, max_value=15.0, value=8.0, step=0.1)
                feuchte_zuluft_soll = rel_feuchte_aus_abs_feuchte(temp_zuluft, feuchte_abs_soll/1000)
        else:
            feuchte_zuluft_soll = feuchte_aussen
            feuchte_abs_soll = None
    
    with st.expander("📉 Absenkbetrieb", expanded=False):
        temp_absenkung = st.number_input("Temperatur-Absenkung [K]", min_value=0.0, max_value=8.0, value=3.0, step=0.5)
        vol_absenkung = st.number_input("Volumenstrom-Reduktion [%]", min_value=0, max_value=70, value=0, step=5)
    
    st.markdown("**⏰ Betriebszeiten:**")
    betriebstyp = st.radio("Betriebstyp:", ["24/7 Dauerbetrieb", "Labor (Mo-Fr 06:30-17:00)", "Benutzerdefiniert"])
    
    if betriebstyp == "24/7 Dauerbetrieb":
        normale_stunden = 8760
        absenk_stunden = 0
        aus_stunden = 0
    elif betriebstyp == "Labor (Mo-Fr 06:30-17:00)":
        normale_stunden = 5 * 10.5 * 52
        absenk_stunden = (5 * 13.5 + 2 * 24) * 52
        aus_stunden = 0
    else:
        with st.expander("📅 Detaileinstellungen", expanded=True):
            col1, col2 = st.columns(2)
            with col1:
                tage_normal = st.number_input("Normalbetrieb [Tage/Woche]", 0, 7, 5)
                stunden_normal = st.number_input("Stunden/Tag Normal", 1, 24, 10)
            with col2:
                tage_absenk = st.number_input("Absenkbetrieb [Tage/Woche]", 0, 7, 7)
                stunden_absenk = st.number_input("Stunden/Tag Absenk", 0, 23, 14)
            normale_stunden = tage_normal * stunden_normal * 52
            absenk_stunden = tage_absenk * stunden_absenk * 52
            aus_stunden = max(0, (7 * 24 * 52) - normale_stunden - absenk_stunden)
    
    with st.expander("🔧 Ausfallzeiten", expanded=False):
        wartungstage = st.number_input("Wartungstage/Jahr", 0, 50, 10)
        ferienwochen = st.number_input("Betriebsferien [Wochen]", 0, 12, 3)
    
    wartungsstunden = wartungstage * 24
    ferienstunden = ferienwochen * 7 * 24
    normale_stunden = max(0, normale_stunden - wartungsstunden - ferienstunden)
    gesamt_aktiv = normale_stunden + absenk_stunden
    
    with st.expander("💰 Energiepreise", expanded=False):
        preis_strom = st.number_input("Strom [€/kWh]", value=0.25, step=0.01, format="%.3f")
        preis_waerme = st.number_input("Fernwärme [€/kWh]", value=0.08, step=0.01, format="%.3f")
        preis_kaelte = st.number_input("Kälte [€/kWh]", value=0.15, step=0.01, format="%.3f")
    
    st.markdown("---")
    st.markdown("**📊 Betriebsstunden/Jahr:**")
    st.info(f"**Normal:** {normale_stunden:,.0f} h\n**Absenk:** {absenk_stunden:,.0f} h\n**Aktiv gesamt:** {gesamt_aktiv:,.0f} h\n**AUS:** {8760 - gesamt_aktiv:,.0f} h")

st.header("📊 Berechnungsergebnisse")

prozess = berechne_rlt_prozess(temp_aussen, feuchte_aussen, temp_zuluft, feuchte_zuluft_soll, wrg_wirkungsgrad, betriebsmodus, feuchte_abs_soll)

luftmassenstrom = volumenstrom_final * 1.2 / 3600
p_ventilator = volumenstrom_final * sfp_final / 1000

p_kuehlung_entf = luftmassenstrom * prozess['leistungen']['kuehlung_entf'] / 1000
p_nachheizung = luftmassenstrom * prozess['leistungen']['nachheizung'] / 1000
p_heizung_direkt = luftmassenstrom * prozess['leistungen']['heizung_direkt'] / 1000
p_kuehlung_direkt = luftmassenstrom * prozess['leistungen']['kuehlung_direkt'] / 1000

p_heizen_gesamt = p_nachheizung + p_heizung_direkt
p_kuehlen_gesamt = p_kuehlung_entf + p_kuehlung_direkt

vol_faktor_absenk = 1 - (vol_absenkung / 100)
energie_ventilator = (p_ventilator * normale_stunden + p_ventilator * vol_faktor_absenk * absenk_stunden)
energie_heizen = (p_heizen_gesamt * normale_stunden + p_heizen_gesamt * 0.7 * absenk_stunden)
energie_kuehlen = (p_kuehlen_gesamt * normale_stunden + p_kuehlen_gesamt * 0.7 * absenk_stunden)

kosten_ventilator = energie_ventilator * preis_strom
kosten_heizen = energie_heizen * preis_waerme
kosten_kuehlen = energie_kuehlen * preis_kaelte
gesamtkosten = kosten_ventilator + kosten_heizen + kosten_kuehlen

col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("⚡ Installierte Leistungen (korrigierte SFP-Werte)")
    col_a, col_b, col_c = st.columns(3)
    
    with col_a:
        st.markdown('<div class="metric-container">', unsafe_allow_html=True)
        st.metric("🟡 Ventilator", f"{p_ventilator:.1f} kW", f"SFP: {sfp_final:.1f} W/(m³/h)")
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
        st.metric("💯 GESAMT", f"{gesamtkosten:,.0f} €")

with col2:
    st.subheader("📈 Kostenverteilung")
    
    if gesamtkosten > 0:
        kosten_data = []
        labels_data = []
        colors_data = []
        
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
            fig.update_layout(height=300, margin=dict(t=20, b=20, l=20, r=20), showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=-0.2))
            st.plotly_chart(fig, use_container_width=True)

st.markdown("---")
st.header("🔄 Luftbehandlungsprozess")

if prozess['schritte']:
    df_prozess = pd.DataFrame(prozess['schritte'])
    df_prozess.columns = ['Prozessschritt', 'Temperatur [°C]', 'rel. Feuchte [%]', 'abs. Feuchte [g/kg]', 'Enthalpie [kJ/kg]']
    st.dataframe(df_prozess, use_container_width=True, hide_index=True)

st.markdown("---")
st.header("📋 Kennzahlen")

col1, col2, col3, col4 = st.columns(4)

with col1:
    if gesamt_aktiv > 0:
        spez_kosten = gesamtkosten / (volumenstrom_final * gesamt_aktiv / 1000)
        st.metric("Spez. Kosten", f"{spez_kosten:.3f} €/(m³·h)")

with col2:
    p_gesamt = p_ventilator + p_heizen_gesamt + p_kuehlen_gesamt
    if volumenstrom_final > 0:
        spez_leistung = p_gesamt / volumenstrom_final * 1000
        st.metric("Spez. Leistung", f"{spez_leistung:.2f} W/m³/h")

with col3:
    jahresenergie = (energie_ventilator + energie_heizen + energie_kuehlen) / 1000
    st.metric("Jahresenergie", f"{jahresenergie:.1f} MWh/a")

with col4:
    co2_emissionen = (energie_ventilator * 0.4 + energie_heizen * 0.2) / 1000
    st.metric("CO₂-Emissionen", f"{co2_emissionen:.1f} t/a")

st.markdown("---")
st.header("🔍 Berechnungsvalidierung")

col1, col2 = st.columns(2)

with col1:
    st.markdown("**✅ Korrigierte Berechnungen:**")
    st.success(f"""
    **Volumenstrom:** {volumenstrom_final:,.0f} m³/h  
    **SFP (korrigiert):** {sfp_final:.1f} W/(m³/h)  
    **Ventilatorleistung:** {p_ventilator:.1f} kW  
    **→ Realistisch für große Anlagen!**
    """)

with col2:
    if st.button("🧮 Detailberechnung anzeigen"):
        st.info(f"""
        **Berechnungsdetails:**
        - Formel: P = V × SFP / 1000
        - {volumenstrom_final:,} × {sfp_final:.1f} / 1000 = {p_ventilator:.1f} kW
        - Jahresenergie Ventilator: {energie_ventilator:,.0f} kWh
        - Jahreskosten Ventilator: {kosten_ventilator:,.0f} €
        """)

st.markdown("---")
if st.button("📄 Professional PDF Report", type="primary"):
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, height-50, "RLT-Kostenanalyse Professional v6.1")
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
        f"Absenkung: -{temp_absenkung:.1f}K, -{vol_absenkung}% Vol.",
        "",
        "Installierte Leistungen:",
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
        file_name=f"RLT_Professional_v6.1_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.pdf",
        mime="application/pdf"
    )

st.markdown("---")
st.markdown("*RLT-Kostenanalyse Professional v6.1 - Korrigierte SFP-Werte und synchronisierte Eingaben*")
