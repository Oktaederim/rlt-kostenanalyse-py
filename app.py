import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

st.set_page_config(
    page_title="RLT-Anlagen Kostenanalyse Professional v5.0",
    page_icon="🏭",
    layout="wide"
)

# Thermodynamische Funktionen
def saettigungsdampfdruck(temp):
    return 611.2 * np.exp((17.67 * temp) / (temp + 243.5))

def abs_feuchte_aus_rel_feuchte(temp, rel_feuchte):
    es = saettigungsdampfdruck(temp)
    e = rel_feuchte / 100 * es
    return 0.622 * e / (101325 - e)

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

def berechne_luftzustand(temp, rel_feuchte=None, abs_feuchte=None):
    if abs_feuchte is None:
        abs_feuchte = abs_feuchte_aus_rel_feuchte(temp, rel_feuchte)
    
    return {
        'temperatur': round(temp, 1),
        'rel_feuchte': round(rel_feuchte, 1) if rel_feuchte else 0,
        'abs_feuchte': round(abs_feuchte * 1000, 2),
        'enthalpie': round(enthalpie_feuchte_luft(temp, abs_feuchte) / 1000, 1)
    }

def berechne_rlt_prozess(temp_aussen, feuchte_aussen, temp_zuluft, feuchte_zuluft_soll, wrg_grad, entfeuchten):
    prozess = {
        'schritte': [],
        'leistungen': {
            'kuehlung_entf': 0,
            'nachheizung': 0,
            'heizung_direkt': 0,
            'kuehlung_direkt': 0
        }
    }
    
    # Außenluft
    abs_feuchte_aussen = abs_feuchte_aus_rel_feuchte(temp_aussen, feuchte_aussen)
    zustand_aussen = berechne_luftzustand(temp_aussen, feuchte_aussen)
    zustand_aussen['punkt'] = 'Außenluft'
    prozess['schritte'].append(zustand_aussen)
    
    aktuelle_temp = temp_aussen
    aktuelle_abs_feuchte = abs_feuchte_aussen
    
    # WRG
    if wrg_grad > 0:
        temp_nach_wrg = temp_aussen + (temp_zuluft - temp_aussen) * wrg_grad / 100
        zustand_wrg = berechne_luftzustand(temp_nach_wrg, abs_feuchte=abs_feuchte_aussen)
        zustand_wrg['punkt'] = 'Nach WRG'
        prozess['schritte'].append(zustand_wrg)
        aktuelle_temp = temp_nach_wrg
    
    # Entfeuchtung
    if entfeuchten:
        ziel_abs_feuchte = abs_feuchte_aus_rel_feuchte(temp_zuluft, feuchte_zuluft_soll)
        if aktuelle_abs_feuchte > ziel_abs_feuchte:
            temp_kuehlung = kuhltemperatur_fur_entfeuchtung(ziel_abs_feuchte)
            zustand_gekuehlt = berechne_luftzustand(temp_kuehlung, abs_feuchte=ziel_abs_feuchte)
            zustand_gekuehlt['punkt'] = f'Gekühlt ({temp_kuehlung:.1f}°C)'
            prozess['schritte'].append(zustand_gekuehlt)
            
            # Kühlenergie
            enthalpie_vorher = enthalpie_feuchte_luft(aktuelle_temp, aktuelle_abs_feuchte)
            enthalpie_nachher = enthalpie_feuchte_luft(temp_kuehlung, ziel_abs_feuchte)
            prozess['leistungen']['kuehlung_entf'] = enthalpie_vorher - enthalpie_nachher
            
            aktuelle_temp = temp_kuehlung
            aktuelle_abs_feuchte = ziel_abs_feuchte
            
            # Nachheizung
            if temp_kuehlung < temp_zuluft:
                zustand_nachgeheizt = berechne_luftzustand(temp_zuluft, abs_feuchte=ziel_abs_feuchte)
                zustand_nachgeheizt['punkt'] = 'Nachheizung'
                prozess['schritte'].append(zustand_nachgeheizt)
                
                enthalpie_vorher = enthalpie_feuchte_luft(temp_kuehlung, ziel_abs_feuchte)
                enthalpie_nachher = enthalpie_feuchte_luft(temp_zuluft, ziel_abs_feuchte)
                prozess['leistungen']['nachheizung'] = enthalpie_nachher - enthalpie_vorher
    
    else:
        # Direkte Temperierung
        if aktuelle_temp < temp_zuluft:
            zustand_final = berechne_luftzustand(temp_zuluft, abs_feuchte=aktuelle_abs_feuchte)
            zustand_final['punkt'] = 'Erwärmung'
            prozess['schritte'].append(zustand_final)
            
            enthalpie_vorher = enthalpie_feuchte_luft(aktuelle_temp, aktuelle_abs_feuchte)
            enthalpie_nachher = enthalpie_feuchte_luft(temp_zuluft, aktuelle_abs_feuchte)
            prozess['leistungen']['heizung_direkt'] = enthalpie_nachher - enthalpie_vorher
            
        elif aktuelle_temp > temp_zuluft:
            zustand_final = berechne_luftzustand(temp_zuluft, abs_feuchte=aktuelle_abs_feuchte)
            zustand_final['punkt'] = 'Kühlung'
            prozess['schritte'].append(zustand_final)
            
            enthalpie_vorher = enthalpie_feuchte_luft(aktuelle_temp, aktuelle_abs_feuchte)
            enthalpie_nachher = enthalpie_feuchte_luft(temp_zuluft, aktuelle_abs_feuchte)
            prozess['leistungen']['kuehlung_direkt'] = enthalpie_vorher - enthalpie_nachher
    
    return prozess

# CSS für bessere Darstellung
st.markdown("""
<style>
    .main-header {
        font-size: 2rem;
        font-weight: bold;
        color: #2c3e50;
        margin-bottom: 1rem;
    }
    .metric-card {
        background-color: #f8f9fa;
        padding: 1rem;
        border-radius: 0.5rem;
        border-left: 4px solid #3498db;
        margin: 0.5rem 0;
    }
    .stMetric {
        font-size: 1.2rem;
    }
    div[data-testid="stSidebar"] {
        width: 350px;
    }
</style>
""", unsafe_allow_html=True)

# Haupttitel
st.markdown('<div class="main-header">🏭 RLT-Kostenanalyse Professional v5.0</div>', unsafe_allow_html=True)
st.markdown("---")

# Sidebar für Eingaben
with st.sidebar:
    st.header("⚙️ Eingabeparameter")
    
    # 1. Anlagenparameter
    with st.expander("🏭 Anlagenparameter", expanded=True):
        volumenstrom = st.number_input("Volumenstrom [m³/h]", 
                                     min_value=100, max_value=500000, value=10000, step=100)
        
        sfp = st.number_input("SFP [W/(m³/h)]", 
                            min_value=0.5, max_value=8.0, value=2.5, step=0.1)
    
    # 2. Betriebsmodi
    with st.expander("🔧 Betriebsmodi", expanded=True):
        entfeuchten = st.checkbox("Entfeuchtung aktiv")
        wrg_wirkungsgrad = st.number_input("WRG-Wirkungsgrad [%]", 
                                         min_value=0, max_value=95, value=70, step=1)
    
    # 3. Klimabedingungen
    with st.expander("🌤️ Klimabedingungen", expanded=True):
        st.write("**Außenluft:**")
        temp_aussen = st.number_input("Außentemperatur [°C]", 
                                    min_value=-20.0, max_value=45.0, value=8.0, step=0.5)
        feuchte_aussen = st.number_input("Außenluft rel. Feuchte [%]", 
                                       min_value=20, max_value=95, value=65, step=1)
        
        st.write("**Zuluft:**")
        temp_zuluft = st.number_input("Zuluft-Solltemperatur [°C]", 
                                    min_value=16.0, max_value=26.0, value=20.0, step=0.5)
        
        if entfeuchten:
            feuchte_zuluft_soll = st.number_input("Zuluft rel. Feuchte [%]", 
                                                min_value=30, max_value=65, value=45, step=1)
        else:
            feuchte_zuluft_soll = feuchte_aussen
    
    # 4. Absenkbetrieb
    with st.expander("📉 Absenkbetrieb", expanded=True):
        temp_absenkung = st.number_input("Temperatur-Absenkung [K]", 
                                       min_value=0.0, max_value=8.0, value=3.0, step=0.5)
        vol_absenkung = st.number_input("Volumenstrom-Reduktion [%]", 
                                      min_value=0, max_value=70, value=30, step=5)
    
    # 5. Kompakter Wochenplaner
    with st.expander("⏰ Betriebszeiten", expanded=True):
        st.write("**Schnellauswahl:**")
        betriebstyp = st.selectbox("Betriebstyp wählen:", 
                                 ["Büro (Mo-Fr 8-18h)", "24/7 Dauerbetrieb", "Benutzerdefiniert"])
        
        if betriebstyp == "Büro (Mo-Fr 8-18h)":
            normale_stunden = 5 * 10 * 52  # 5 Tage * 10h * 52 Wochen
            absenk_stunden = 5 * 14 * 52   # 5 Tage * 14h * 52 Wochen
            aus_stunden = 2 * 24 * 52      # 2 Tage * 24h * 52 Wochen
        elif betriebstyp == "24/7 Dauerbetrieb":
            normale_stunden = 24 * 365
            absenk_stunden = 0
            aus_stunden = 0
        else:  # Benutzerdefiniert
            col1, col2 = st.columns(2)
            with col1:
                tage_normal = st.number_input("Normalbetrieb [Tage/Woche]", 0, 7, 5)
                stunden_normal = st.number_input("Stunden/Tag Normal", 1, 24, 10)
            with col2:
                tage_absenk = st.number_input("Absenkbetrieb [Tage/Woche]", 0, 7, 5)
                stunden_absenk = st.number_input("Stunden/Tag Absenk", 0, 23, 14)
            
            normale_stunden = tage_normal * stunden_normal * 52
            absenk_stunden = tage_absenk * stunden_absenk * 52
            aus_stunden = (7 * 24 * 52) - normale_stunden - absenk_stunden
        
        # Ausfallzeiten
        wartungstage = st.number_input("Wartungstage/Jahr", 0, 50, 10)
        ferienwochen = st.number_input("Betriebsferien [Wochen]", 0, 12, 3)
        
        # Korrekturen
        wartungsstunden = wartungstage * 24
        ferienstunden = ferienwochen * 7 * 24
        normale_stunden = max(0, normale_stunden - wartungsstunden - ferienstunden)
        
        gesamt_aktiv = normale_stunden + absenk_stunden
        
        # Anzeige
        st.info(f"""
        **Betriebsstunden/Jahr:**
        - Normal: {normale_stunden:,.0f} h
        - Absenk: {absenk_stunden:,.0f} h  
        - Aktiv gesamt: {gesamt_aktiv:,.0f} h
        - AUS: {8760 - gesamt_aktiv:,.0f} h
        """)
    
    # 6. Energiepreise
    with st.expander("💰 Energiepreise", expanded=True):
        preis_strom = st.number_input("Strom [€/kWh]", value=0.25, step=0.01, format="%.3f")
        preis_waerme = st.number_input("Fernwärme [€/kWh]", value=0.08, step=0.01, format="%.3f")
        preis_kaelte = st.number_input("Kälte [€/kWh]", value=0.15, step=0.01, format="%.3f")

# Hauptbereich mit sofortiger Berechnung
col1, col2 = st.columns([3, 2])

with col1:
    st.header("📊 Berechnungsergebnisse")
    
    # Berechnungen (werden bei jeder Eingabeänderung neu ausgeführt)
    prozess = berechne_rlt_prozess(temp_aussen, feuchte_aussen, temp_zuluft, 
                                 feuchte_zuluft_soll, wrg_wirkungsgrad, entfeuchten)
    
    # Leistungsberechnungen
    luftmassenstrom = volumenstrom * 1.2 / 3600  # kg/s
    
    # Ventilatorleistung
    p_ventilator = volumenstrom * sfp / 1000  # kW
    
    # Thermische Leistungen
    p_kuehlung_entf = luftmassenstrom * prozess['leistungen']['kuehlung_entf'] / 1000
    p_nachheizung = luftmassenstrom * prozess['leistungen']['nachheizung'] / 1000
    p_heizung_direkt = luftmassenstrom * prozess['leistungen']['heizung_direkt'] / 1000
    p_kuehlung_direkt = luftmassenstrom * prozess['leistungen']['kuehlung_direkt'] / 1000
    
    p_heizen_gesamt = p_nachheizung + p_heizung_direkt
    p_kuehlen_gesamt = p_kuehlung_entf + p_kuehlung_direkt
    
    # Jahresenergie (mit Absenkbetrieb)
    # Normal: volle Leistung
    # Absenk: reduzierte Leistung
    vol_faktor_absenk = 1 - (vol_absenkung / 100)
    
    energie_ventilator = (p_ventilator * normale_stunden + 
                        p_ventilator * vol_faktor_absenk * absenk_stunden)
    
    energie_heizen = (p_heizen_gesamt * normale_stunden + 
                    p_heizen_gesamt * 0.7 * absenk_stunden)  # 30% weniger bei Absenkung
    
    energie_kuehlen = (p_kuehlen_gesamt * normale_stunden + 
                     p_kuehlen_gesamt * 0.7 * absenk_stunden)
    
    # Jahreskosten
    kosten_ventilator = energie_ventilator * preis_strom
    kosten_heizen = energie_heizen * preis_waerme
    kosten_kuehlen = energie_kuehlen * preis_kaelte
    gesamtkosten = kosten_ventilator + kosten_heizen + kosten_kuehlen
    
    # Ergebnisse anzeigen
    st.subheader("⚡ Installierte Leistungen")
    
    col_a, col_b, col_c = st.columns(3)
    
    with col_a:
        st.metric("🟡 Ventilator", f"{p_ventilator:.1f} kW")
    
    with col_b:
        st.metric("🔴 Heizung", f"{p_heizen_gesamt:.1f} kW")
        if p_nachheizung > 0.1:
            st.caption(f"Nachheizung: {p_nachheizung:.1f} kW")
    
    with col_c:
        st.metric("🔵 Kühlung", f"{p_kuehlen_gesamt:.1f} kW")
        if p_kuehlung_entf > 0.1:
            st.caption(f"Entfeuchtung: {p_kuehlung_entf:.1f} kW")
    
    st.markdown("---")
    st.subheader("💰 Jahreskosten")
    
    col_a, col_b, col_c, col_d = st.columns(4)
    
    with col_a:
        st.metric("🟡 Ventilator", f"{kosten_ventilator:.0f} €/a")
    
    with col_b:
        st.metric("🔴 Heizung", f"{kosten_heizen:.0f} €/a")
    
    with col_c:
        st.metric("🔵 Kühlung", f"{kosten_kuehlen:.0f} €/a")
    
    with col_d:
        st.metric("💯 Gesamt", f"{gesamtkosten:.0f} €/a")

with col2:
    st.header("📈 Visualisierung")
    
    # Kostenverteilung
    if gesamtkosten > 0:
        kosten_data = []
        labels_data = []
        
        if kosten_ventilator > 0:
            kosten_data.append(kosten_ventilator)
            labels_data.append("Ventilator")
        
        if kosten_heizen > 0:
            kosten_data.append(kosten_heizen)
            labels_data.append("Heizung")
        
        if kosten_kuehlen > 0:
            kosten_data.append(kosten_kuehlen)
            labels_data.append("Kühlung")
        
        if kosten_data:
            fig = go.Figure(data=[go.Pie(
                labels=labels_data,
                values=kosten_data,
                hole=0.4
            )])
            fig.update_traces(textposition='inside', textinfo='percent+label')
            fig.update_layout(height=300, margin=dict(t=0, b=0, l=0, r=0))
            st.plotly_chart(fig, use_container_width=True)

# Prozessdarstellung
st.markdown("---")
st.header("🔄 Luftbehandlungsprozess")

if prozess['schritte']:
    df_prozess = pd.DataFrame(prozess['schritte'])
    df_prozess.columns = ['Prozessschritt', 'Temperatur [°C]', 'rel. Feuchte [%]', 
                         'abs. Feuchte [g/kg]', 'Enthalpie [kJ/kg]']
    st.dataframe(df_prozess, use_container_width=True, hide_index=True)

# Kennzahlen
st.markdown("---")
st.header("📋 Kennzahlen")

col1, col2, col3, col4 = st.columns(4)

with col1:
    if gesamt_aktiv > 0:
        spez_kosten = gesamtkosten / (volumenstrom * gesamt_aktiv / 1000)
        st.metric("Spez. Kosten", f"{spez_kosten:.3f} €/(m³·h)")

with col2:
    p_gesamt = p_ventilator + p_heizen_gesamt + p_kuehlen_gesamt
    spez_leistung = p_gesamt / volumenstrom * 1000
    st.metric("Spez. Leistung", f"{spez_leistung:.2f} W/m³/h")

with col3:
    jahresenergie = (energie_ventilator + energie_heizen + energie_kuehlen) / 1000
    st.metric("Jahresenergie", f"{jahresenergie:.1f} MWh/a")

with col4:
    co2_emissionen = (energie_ventilator * 0.4 + energie_heizen * 0.2) / 1000
    st.metric("CO₂-Emissionen", f"{co2_emissionen:.1f} t/a")

# PDF Export
st.markdown("---")
if st.button("📄 Professional PDF Report", type="primary"):
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    # Header
    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, height-50, "RLT-Kostenanalyse Professional v5.0")
    p.setFont("Helvetica", 10)
    p.drawString(50, height-70, f"Erstellt: {pd.Timestamp.now().strftime('%d.%m.%Y %H:%M')}")
    
    y = height - 110
    p.setFont("Helvetica", 11)
    
    # Daten
    daten = [
        f"Volumenstrom: {volumenstrom:,} m³/h",
        f"SFP: {sfp:.1f} W/(m³/h)",
        f"WRG: {wrg_wirkungsgrad}%",
        f"Außenluft: {temp_aussen:.1f}°C, {feuchte_aussen}% rF",
        f"Zuluft: {temp_zuluft:.1f}°C" + (f", {feuchte_zuluft_soll}% rF" if entfeuchten else ""),
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
        f"GESAMTKOSTEN: {gesamtkosten:,.0f} €/Jahr"
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
        file_name=f"RLT_Professional_v5_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.pdf",
        mime="application/pdf"
    )

st.markdown("---")
st.markdown("*RLT-Kostenanalyse Professional v5.0 - Verbesserte Benutzerfreundlichkeit und Performance*")
