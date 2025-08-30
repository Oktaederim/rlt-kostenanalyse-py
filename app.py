import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import plotly.graph_objects as go
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm

# Seiten-Konfiguration
st.set_page_config(
    page_title="RLT-Anlagen Kostenanalyse Professional",
    page_icon="🏭",
    layout="wide"
)

def berechne_luftzustand(temp, rel_feuchte):
    """Vereinfachte Luftzustandsberechnung ohne psychrolib"""
    try:
        # Sättigungsdampfdruck nach Magnus-Formel
        es = 611.2 * np.exp((17.67 * temp) / (temp + 243.5))
        # Dampfdruck
        e = rel_feuchte / 100 * es
        # Absolute Feuchte (approximiert)
        abs_feuchte = 0.622 * e / (101325 - e)
        # Enthalpie (approximiert)
        enthalpie = 1005 * temp + abs_feuchte * (2501000 + 1870 * temp)
        # Taupunkt (approximiert)
        if e > 0:
            taupunkt = 243.5 * np.log(e/611.2) / (17.67 - np.log(e/611.2))
        else:
            taupunkt = temp
        
        return {
            'temperatur': temp,
            'rel_feuchte': rel_feuchte,
            'abs_feuchte': abs_feuchte,
            'enthalpie': enthalpie,
            'taupunkt': taupunkt,
            'punkt': 'Berechnet'
        }
    except Exception as e:
        st.error(f"Fehler bei Luftzustandsberechnung: {e}")
        return {
            'temperatur': temp,
            'rel_feuchte': rel_feuchte,
            'abs_feuchte': 0.01,
            'enthalpie': 1005 * temp,
            'taupunkt': temp,
            'punkt': 'Vereinfacht'
        }

def berechne_rlt_prozess(aussen, soll_temp, soll_feuchte_abs, wrg_grad, betriebsmodi):
    """Berechnet vereinfachten RLT-Prozess"""
    prozess = {
        'schritte': [],
        'heizung': 0,
        'kuehlung': 0,
        'kuehlung_entf': 0
    }
    
    # Schritt 1: Außenluft
    zustand = aussen.copy()
    zustand['punkt'] = 'Außenluft'
    prozess['schritte'].append(zustand)
    
    # Schritt 2: Nach WRG (wenn vorhanden)
    if wrg_grad > 0:
        temp_nach_wrg = aussen['temperatur'] + (soll_temp - aussen['temperatur']) * wrg_grad
        zustand_wrg = berechne_luftzustand(temp_nach_wrg, aussen['rel_feuchte'])
        zustand_wrg['punkt'] = 'Nach WRG'
        prozess['schritte'].append(zustand_wrg)
        zustand = zustand_wrg
    
    # Schritt 3: Entfeuchtung (wenn aktiv und erforderlich)
    if betriebsmodi['entfeuchten'] and soll_feuchte_abs:
        if zustand['abs_feuchte'] > soll_feuchte_abs:
            # Vereinfachte Entfeuchtung: Kühlen auf 12°C, dann aufheizen
            zustand_entf = berechne_luftzustand(12, 95)  # Kühl-/Entfeuchtungstemperatur
            zustand_entf['punkt'] = 'Nach Entfeuchtung'
            prozess['schritte'].append(zustand_entf)
            
            # Entfeuchtungsenergie
            prozess['kuehlung_entf'] = abs(zustand['enthalpie'] - zustand_entf['enthalpie'])
            zustand = zustand_entf
    
    # Schritt 4: Finale Temperierung
    if zustand['temperatur'] != soll_temp:
        zustand_final = berechne_luftzustand(soll_temp, zustand['rel_feuchte'])
        zustand_final['punkt'] = 'Zuluft'
        prozess['schritte'].append(zustand_final)
        
        # Heiz-/Kühlenergie
        energie_diff = zustand_final['enthalpie'] - zustand['enthalpie']
        if energie_diff > 0:
            prozess['heizung'] = energie_diff
        else:
            prozess['kuehlung'] = abs(energie_diff)
    
    return prozess

# Haupttitel
st.title("🏭 RLT-Anlagen Kostenanalyse Professional")
st.markdown("*Professionelle Kostenberechnung für RLT-Anlagen*")
st.markdown("---")

# Sidebar für Eingaben
with st.sidebar:
    st.header("📋 Anlagendaten")
    
    # Grunddaten
    volumenstrom = st.number_input("Volumenstrom [m³/h]", value=10000, min_value=100, step=500)
    betriebsstunden_tag = st.slider("Betriebsstunden/Tag", 1, 24, 12)
    betriebstage_jahr = st.number_input("Betriebstage/Jahr", value=250, min_value=50, max_value=365)
    teillast_faktor = st.slider("Ø Teillast [%]", 30, 100, 80) / 100
    
    st.markdown("---")
    st.subheader("🌡️ Klimabedingungen")
    
    temp_aussen = st.slider("Außentemperatur [°C]", -20, 40, 5)
    feuchte_aussen = st.slider("Außenluft rel. Feuchte [%]", 20, 95, 60)
    temp_zuluft = st.slider("Zuluft-Solltemperatur [°C]", 16, 26, 20)
        
    st.markdown("---")
    st.subheader("⚙️ Betriebsmodi")
    
    entfeuchten = st.checkbox("🌊 Entfeuchtung aktiv", False)
    
    soll_feuchte_abs = None
    if entfeuchten:
        feuchte_zuluft_rel = st.slider("Zuluft-Sollfeuchte [%]", 30, 70, 50)
        # Vereinfachte Berechnung der absoluten Sollfeuchte
        es_soll = 611.2 * np.exp((17.67 * temp_zuluft) / (temp_zuluft + 243.5))
        e_soll = feuchte_zuluft_rel / 100 * es_soll
        soll_feuchte_abs = 0.622 * e_soll / (101325 - e_soll)
        st.info(f"💡 Entfeuchtung: Kühlen → Kondensieren → Nachheizen")
    else:
        st.info("💡 Nur Temperaturregelung")
    
    # Betriebsmodi
    betriebsmodi = {
        'entfeuchten': entfeuchten,
        'heizen': temp_zuluft > temp_aussen,
        'kuehlen': temp_zuluft < temp_aussen or entfeuchten
    }
    
    st.markdown("---")
    st.subheader("🔄 Wärmerückgewinnung")
    wrg_vorhanden = st.checkbox("WRG vorhanden", True)
    if wrg_vorhanden:
        wrg_wirkungsgrad = st.slider("WRG-Wirkungsgrad [%]", 50, 90, 70) / 100
        wrg_typ = st.selectbox("WRG-Typ", ["Plattenwärmetauscher", "Rotationswärmetauscher", "KVS"])
    else:
        wrg_wirkungsgrad = 0
        wrg_typ = "Keine"

# Berechnungen durchführen
try:
    aussen_zustand = berechne_luftzustand(temp_aussen, feuchte_aussen)
    prozess = berechne_rlt_prozess(aussen_zustand, temp_zuluft, soll_feuchte_abs, wrg_wirkungsgrad, betriebsmodi)
except Exception as e:
    st.error(f"Fehler bei der Berechnung: {e}")
    # Fallback-Werte
    aussen_zustand = {
        'temperatur': temp_aussen,
        'rel_feuchte': feuchte_aussen,
        'abs_feuchte': 0.01,
        'enthalpie': 1005 * temp_aussen,
        'punkt': 'Außenluft'
    }
    prozess = {
        'schritte': [aussen_zustand],
        'heizung': max(0, (temp_zuluft - temp_aussen) * 1005) if temp_zuluft > temp_aussen else 0,
        'kuehlung': max(0, (temp_aussen - temp_zuluft) * 1005) if temp_zuluft < temp_aussen else 0,
        'kuehlung_entf': 0
    }

# Hauptbereich mit Tabs
tab1, tab2, tab3, tab4 = st.tabs(["📊 Berechnung", "🔄 Prozess", "📈 Diagramme", "📄 Report"])

with tab1:
    col1, col2 = st.columns([1, 2])
    
    with col1:
        st.subheader("⚡ Technische Parameter")
        sfp = st.number_input("Spez. Ventilatorleistung SFP [W/(m³/h)]", 
                             value=2.5, min_value=0.5, max_value=6.0, step=0.1)
        
        st.subheader("💰 Energiepreise")
        preis_strom = st.number_input("Strompreis [€/kWh]", value=0.25, format="%.3f", step=0.01)
        preis_waerme = st.number_input("Fernwärmepreis [€/kWh]", value=0.08, format="%.3f", step=0.01)
        preis_kaelte = st.number_input("Kältepreis [€/kWh]", value=0.15, format="%.3f", step=0.01)
        
        with st.expander("🔧 Zusatzkosten (optional)"):
            wartung_jahr = st.number_input("Wartungskosten [€/Jahr]", value=0, min_value=0)
            filter_kosten = st.number_input("Filterkosten [€/Jahr]", value=0, min_value=0)
    
    with col2:
        st.subheader("📊 Berechnungsergebnisse")
        
        # Leistungsberechnungen
        p_ventilator = volumenstrom * sfp * teillast_faktor / 1000  # kW
        luftmassenstrom = volumenstrom * 1.2 / 3600  # kg/s
        
        # Thermische Leistungen aus Prozess
        p_heizen = luftmassenstrom * prozess.get('heizung', 0) * teillast_faktor / 1000
        p_kuehlen = luftmassenstrom * (prozess.get('kuehlung', 0) + prozess.get('kuehlung_entf', 0)) * teillast_faktor / 1000
        
        # Jahreskosten
        jahresstunden = betriebsstunden_tag * betriebstage_jahr
        kosten_ventilator = p_ventilator * jahresstunden * preis_strom
        kosten_heizen = p_heizen * jahresstunden * preis_waerme
        kosten_kuehlen = p_kuehlen * jahresstunden * preis_kaelte
        kosten_zusatz = wartung_jahr + filter_kosten
        
        # Ergebnisse anzeigen
        col_a, col_b, col_c = st.columns(3)
        
        with col_a:
            st.metric("🌀 Ventilatorleistung", f"{p_ventilator:.1f} kW")
            st.metric("💰 Ventilator/Jahr", f"{kosten_ventilator:.0f} €")
        
        with col_b:
            st.metric("🔥 Heizleistung", f"{p_heizen:.1f} kW")
            st.metric("💰 Heizung/Jahr", f"{kosten_heizen:.0f} €")
        
        with col_c:
            st.metric("❄️ Kühlleistung", f"{p_kuehlen:.1f} kW")
            st.metric("💰 Kühlung/Jahr", f"{kosten_kuehlen:.0f} €")
        
        # Gesamtkosten
        gesamtkosten = kosten_ventilator + kosten_heizen + kosten_kuehlen + kosten_zusatz
        st.markdown("### 💯 Gesamtkosten")
        st.metric("", f"{gesamtkosten:.0f} €/Jahr")
        
        # Spezifische Kosten
        if jahresstunden > 0:
            spez_kosten = gesamtkosten / (volumenstrom * jahresstunden / 1000)
            st.metric("Spezifische Kosten", f"{spez_kosten:.3f} €/(m³·h)")

with tab2:
    st.subheader("🔄 RLT-Prozess Schritt-für-Schritt")
    
    # Sichere Anzeige der Prozessschritte
    if prozess and 'schritte' in prozess and len(prozess['schritte']) > 0:
        for i, schritt in enumerate(prozess['schritte']):
            with st.container():
                col1, col2, col3, col4 = st.columns(4)
                
                with col1:
                    punkt_name = schritt.get('punkt', f'Schritt {i+1}')
                    st.write(f"**{punkt_name}**")
                with col2:
                    temp = schritt.get('temperatur', 0)
                    st.write(f"🌡️ {temp:.1f}°C")
                with col3:
                    feuchte = schritt.get('rel_feuchte', 0)
                    st.write(f"💧 {feuchte:.1f}% rF")
                with col4:
                    enthalpie = schritt.get('enthalpie', 0)
                    st.write(f"⚡ {enthalpie:.0f} J/kg")
                
                if i < len(prozess['schritte']) - 1:
                    st.write("⬇️")
    else:
        st.warning("Keine Prozessschritte verfügbar")
    
    # Aktive Prozesse
    st.markdown("### 🔧 Aktive Prozesse:")
    if wrg_wirkungsgrad > 0:
        st.success(f"✅ Wärmerückgewinnung: {wrg_wirkungsgrad*100:.0f}% ({wrg_typ})")
    if betriebsmodi.get('entfeuchten', False):
        st.info("✅ Entfeuchtung: Kühlung → Kondensation → Nachheizung")
    if betriebsmodi.get('heizen', False):
        st.warning("✅ Heizung erforderlich")
    if betriebsmodi.get('kuehlen', False) and not betriebsmodi.get('entfeuchten', False):
        st.info("✅ Kühlung erforderlich")

with tab3:
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("💰 Kostenverteilung")
        
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
        
        if kosten_zusatz > 0:
            kosten_data.append(kosten_zusatz)
            labels_data.append("Wartung/Filter")
        
        if kosten_data and len(kosten_data) > 0:
            fig = go.Figure(data=[go.Pie(
                labels=labels_data,
                values=kosten_data,
                hole=0.3
            )])
            fig.update_traces(textposition='inside', textinfo='percent+label')
            fig.update_layout(height=400, showlegend=True)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Keine Kostendaten zum Anzeigen vorhanden")
    
    with col2:
        st.subheader("⚡ Leistungsverteilung")
        
        leistungen = []
        leistung_labels = []
        
        if p_ventilator > 0:
            leistungen.append(p_ventilator)
            leistung_labels.append("Ventilator")
        
        if p_heizen > 0:
            leistungen.append(p_heizen)
            leistung_labels.append("Heizung")
        
        if p_kuehlen > 0:
            leistungen.append(p_kuehlen)
            leistung_labels.append("Kühlung")
        
        if leistungen and len(leistungen) > 0:
            fig2 = go.Figure(data=[go.Bar(
                x=leistung_labels,
                y=leistungen
            )])
            fig2.update_layout(
                title="Leistungsverteilung [kW]",
                height=400,
                yaxis_title="Leistung [kW]"
            )
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("Keine Leistungsdaten zum Anzeigen vorhanden")

with tab4:
    st.subheader("📄 Professional Report")
    
    if st.button("📄 PDF-Report generieren", type="primary"):
        buffer = BytesIO()
        p = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4
        
        # Header
        p.setFont("Helvetica-Bold", 16)
        p.drawString(50, height-50, "RLT-Anlagen Kostenanalyse")
        p.setFont("Helvetica", 10)
        p.drawString(50, height-70, f"Erstellt: {pd.Timestamp.now().strftime('%d.%m.%Y %H:%M')}")
        
        # Anlagendaten
        y = height - 120
        p.setFont("Helvetica-Bold", 12)
        p.drawString(50, y, "Anlagendaten:")
        y -= 20
        
        p.setFont("Helvetica", 10)
        daten = [
            f"Volumenstrom: {volumenstrom:,} m³/h",
            f"Betrieb: {betriebsstunden_tag} h/Tag, {betriebstage_jahr} Tage/Jahr",
            f"Teillast: {teillast_faktor*100:.0f}%",
            f"Außenluft: {temp_aussen}°C, {feuchte_aussen}% rF",
            f"Zuluft: {temp_zuluft}°C",
            "",
            f"Ventilator: {p_ventilator:.1f} kW → {kosten_ventilator:.0f} €/Jahr",
            f"Heizung: {p_heizen:.1f} kW → {kosten_heizen:.0f} €/Jahr",
            f"Kühlung: {p_kuehlen:.1f} kW → {kosten_kuehlen:.0f} €/Jahr",
            "",
            f"GESAMTKOSTEN: {gesamtkosten:.0f} €/Jahr"
        ]
        
        for item in daten:
            p.drawString(70, y, item)
            y -= 15
        
        p.save()
        buffer.seek(0)
        
        st.download_button(
            label="📥 PDF herunterladen",
            data=buffer,
            file_name=f"RLT_Analyse_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.pdf",
            mime="application/pdf"
        )

# Footer
st.markdown("---")
st.markdown("*RLT-Anlagen Kostenanalyse Professional v2.1*")
