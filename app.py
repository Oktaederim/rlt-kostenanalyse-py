import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
import calendar

st.set_page_config(
    page_title="RLT-Anlagen Kostenanalyse Professional",
    page_icon="🏭",
    layout="wide"
)

# Farbschema definieren
FARBEN = {
    'heizung': '#E74C3C',
    'heizung_hell': '#FF6B6B',
    'kuehlung': '#2980B9',
    'kuehlung_hell': '#3498DB',
    'aussenluft': '#27AE60',
    'aussenluft_hell': '#2ECC71',
    'ventilator': '#E67E22',
    'ventilator_hell': '#F39C12',
    'wrg': '#8E44AD',
    'wrg_hell': '#9B59B6'
}

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

def berechne_luftzustand(temp, rel_feuchte=None, abs_feuchte=None):
    if abs_feuchte is None:
        abs_feuchte = abs_feuchte_aus_rel_feuchte(temp, rel_feuchte)
    else:
        rel_feuchte = rel_feuchte_aus_abs_feuchte(temp, abs_feuchte)
    
    return {
        'temperatur': round(temp, 1),
        'rel_feuchte': round(rel_feuchte, 1),
        'abs_feuchte': round(abs_feuchte * 1000, 2),
        'enthalpie': round(enthalpie_feuchte_luft(temp, abs_feuchte) / 1000, 1),
        'taupunkt': round(taupunkt_aus_abs_feuchte(abs_feuchte), 1)
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
    zustand_aussen['punkt'] = '🟢 Außenluft'
    prozess['schritte'].append(zustand_aussen)
    
    aktuelle_temp = temp_aussen
    aktuelle_abs_feuchte = abs_feuchte_aussen
    
    # WRG
    if wrg_grad > 0:
        temp_nach_wrg = temp_aussen + (temp_zuluft - temp_aussen) * wrg_grad
        zustand_wrg = berechne_luftzustand(temp_nach_wrg, abs_feuchte=abs_feuchte_aussen)
        zustand_wrg['punkt'] = '🟣 Nach WRG'
        prozess['schritte'].append(zustand_wrg)
        aktuelle_temp = temp_nach_wrg
    
    # Entfeuchtung
    ziel_abs_feuchte = abs_feuchte_aus_rel_feuchte(temp_zuluft, feuchte_zuluft_soll)
    
    if entfeuchten and aktuelle_abs_feuchte > ziel_abs_feuchte:
        temp_kuehlung = kuhltemperatur_fur_entfeuchtung(ziel_abs_feuchte)
        zustand_gekuehlt = berechne_luftzustand(temp_kuehlung, abs_feuchte=ziel_abs_feuchte)
        zustand_gekuehlt['punkt'] = f'🔵 Gekühlt auf {temp_kuehlung:.1f}°C'
        prozess['schritte'].append(zustand_gekuehlt)
        
        # Kühlenergie für Entfeuchtung
        enthalpie_vorher = enthalpie_feuchte_luft(aktuelle_temp, aktuelle_abs_feuchte)
        enthalpie_nachher = enthalpie_feuchte_luft(temp_kuehlung, ziel_abs_feuchte)
        prozess['leistungen']['kuehlung_entf'] = enthalpie_vorher - enthalpie_nachher
        
        aktuelle_temp = temp_kuehlung
        aktuelle_abs_feuchte = ziel_abs_feuchte
        
        # Nachheizung
        if temp_kuehlung < temp_zuluft:
            zustand_nachgeheizt = berechne_luftzustand(temp_zuluft, abs_feuchte=ziel_abs_feuchte)
            zustand_nachgeheizt['punkt'] = '🔴 Nachheizung'
            prozess['schritte'].append(zustand_nachgeheizt)
            
            enthalpie_vorher = enthalpie_feuchte_luft(temp_kuehlung, ziel_abs_feuchte)
            enthalpie_nachher = enthalpie_feuchte_luft(temp_zuluft, ziel_abs_feuchte)
            prozess['leistungen']['nachheizung'] = enthalpie_nachher - enthalpie_vorher
    
    else:
        # Keine Entfeuchtung - direkte Temperierung
        if aktuelle_temp < temp_zuluft:
            zustand_final = berechne_luftzustand(temp_zuluft, abs_feuchte=aktuelle_abs_feuchte)
            zustand_final['punkt'] = '🔴 Erwärmung'
            prozess['schritte'].append(zustand_final)
            
            enthalpie_vorher = enthalpie_feuchte_luft(aktuelle_temp, aktuelle_abs_feuchte)
            enthalpie_nachher = enthalpie_feuchte_luft(temp_zuluft, aktuelle_abs_feuchte)
            prozess['leistungen']['heizung_direkt'] = enthalpie_nachher - enthalpie_vorher
            
        elif aktuelle_temp > temp_zuluft:
            zustand_final = berechne_luftzustand(temp_zuluft, abs_feuchte=aktuelle_abs_feuchte)
            zustand_final['punkt'] = '🔵 Kühlung'
            prozess['schritte'].append(zustand_final)
            
            enthalpie_vorher = enthalpie_feuchte_luft(aktuelle_temp, aktuelle_abs_feuchte)
            enthalpie_nachher = enthalpie_feuchte_luft(temp_zuluft, aktuelle_abs_feuchte)
            prozess['leistungen']['kuehlung_direkt'] = enthalpie_vorher - enthalpie_nachher
    
    return prozess

def berechne_betriebsstunden(betriebszeiten, wartungstage_jahr, ferien_wochen):
    """Berechnet detaillierte Betriebsstunden basierend auf Wochentagsprofilen"""
    
    # Basis: 52 Wochen minus Ferienwochen
    aktive_wochen = 52 - ferien_wochen
    
    stunden_jahr = {
        'normal': 0,
        'absenk': 0,
        'aus': 0
    }
    
    wochentage = ['mo', 'di', 'mi', 'do', 'fr', 'sa', 'so']
    
    for tag in wochentage:
        if betriebszeiten[tag]['aktiv']:
            # Normalbetrieb
            start_normal = betriebszeiten[tag]['normal_von']
            ende_normal = betriebszeiten[tag]['normal_bis']
            normal_stunden_tag = ende_normal - start_normal if ende_normal > start_normal else 0
            
            # Absenkbetrieb
            if betriebszeiten[tag]['absenk_aktiv']:
                # Absenkzeit = 24h minus Normalzeit minus AUS-Zeit
                absenk_stunden_tag = 24 - normal_stunden_tag
            else:
                absenk_stunden_tag = 0
            
            # AUS-Zeit
            aus_stunden_tag = 24 - normal_stunden_tag - absenk_stunden_tag
            
            # Auf Jahr hochrechnen
            stunden_jahr['normal'] += normal_stunden_tag * aktive_wochen
            stunden_jahr['absenk'] += absenk_stunden_tag * aktive_wochen
            stunden_jahr['aus'] += aus_stunden_tag * aktive_wochen
    
    # Wartungszeiten abziehen (aus Normalzeit)
    wartungsstunden = wartungstage_jahr * 24
    stunden_jahr['normal'] = max(0, stunden_jahr['normal'] - wartungsstunden)
    stunden_jahr['aus'] += wartungsstunden
    
    stunden_jahr['gesamt'] = stunden_jahr['normal'] + stunden_jahr['absenk']
    
    return stunden_jahr

# CSS für farbige Metriken
st.markdown("""
<style>
.metric-heizung {
    background-color: rgba(231, 76, 60, 0.1);
    border-left: 4px solid #E74C3C;
    padding: 10px;
    border-radius: 5px;
}
.metric-kuehlung {
    background-color: rgba(41, 128, 185, 0.1);
    border-left: 4px solid #2980B9;
    padding: 10px;
    border-radius: 5px;
}
.metric-ventilator {
    background-color: rgba(230, 126, 34, 0.1);
    border-left: 4px solid #E67E22;
    padding: 10px;
    border-radius: 5px;
}
.metric-wrg {
    background-color: rgba(142, 68, 173, 0.1);
    border-left: 4px solid #8E44AD;
    padding: 10px;
    border-radius: 5px;
}
</style>
""", unsafe_allow_html=True)

# Haupttitel
st.title("🏭 RLT-Anlagen Kostenanalyse Professional v4.0")
st.markdown("*Fachlich korrekte Kostenberechnung mit professioneller Benutzerführung*")
st.markdown("---")

# Layout
col_left, col_right = st.columns([1, 2])

with col_left:
    st.header("⚙️ Konfiguration")
    
    # 1. ANLAGENPARAMETER
    with st.expander("📋 Anlagenparameter", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            volumenstrom_slider = st.slider("Volumenstrom [m³/h]", 100, 500000, 10000, step=500)
        with col2:
            volumenstrom = st.number_input("Exakter Wert", value=volumenstrom_slider, min_value=100, max_value=500000, step=100)
        
        col1, col2 = st.columns(2)
        with col1:
            sfp_slider = st.slider("SFP [W/(m³/h)]", 0.5, 6.0, 2.5, step=0.1)
        with col2:
            sfp = st.number_input("SFP exakt", value=sfp_slider, min_value=0.5, max_value=6.0, step=0.1)
        
        col1, col2 = st.columns(2)
        with col1:
            teillast_normal = st.slider("Teillast Normal [%]", 50, 100, 85) / 100
        with col2:
            teillast_absenk = st.slider("Teillast Absenk [%]", 20, 80, 60) / 100

    # 2. BETRIEBSMODI
    with st.expander("🔧 Betriebsmodi", expanded=True):
        entfeuchten = st.checkbox("🌊 Entfeuchtung aktiv", False)
        
        st.markdown("**🟣 Wärmerückgewinnung:**")
        wrg_vorhanden = st.checkbox("WRG vorhanden", True)
        if wrg_vorhanden:
            col1, col2 = st.columns(2)
            with col1:
                wrg_wirkungsgrad = st.slider("WRG-Wirkungsgrad [%]", 50, 95, 70) / 100
            with col2:
                wrg_typ = st.selectbox("WRG-Typ", ["Plattenwärmetauscher", "Rotationswärmetauscher", "KVS"])
        else:
            wrg_wirkungsgrad = 0
            wrg_typ = "Keine"

    # 3. BETRIEBSZEITEN (Wochentagsbasiert)
    with st.expander("⏰ Detaillierte Betriebszeiten", expanded=True):
        st.markdown("**Wochentagsprofil definieren:**")
        
        betriebszeiten = {}
        wochentage_namen = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']
        wochentage_kurz = ['mo', 'di', 'mi', 'do', 'fr', 'sa', 'so']
        
        # Schnellauswahl
        st.markdown("**Schnellauswahl:**")
        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("Mo-Fr Bürozeiten"):
                st.session_state.update({f'{tag}_aktiv': tag in ['mo', 'di', 'mi', 'do', 'fr'] for tag in wochentage_kurz})
                st.session_state.update({f'{tag}_normal_von': 8 for tag in ['mo', 'di', 'mi', 'do', 'fr']})
                st.session_state.update({f'{tag}_normal_bis': 18 for tag in ['mo', 'di', 'mi', 'do', 'fr']})
        
        with col2:
            if st.button("24/7 Dauerbetrieb"):
                st.session_state.update({f'{tag}_aktiv': True for tag in wochentage_kurz})
                st.session_state.update({f'{tag}_normal_von': 0 for tag in wochentage_kurz})
                st.session_state.update({f'{tag}_normal_bis': 24 for tag in wochentage_kurz})
        
        with col3:
            if st.button("Alle AUS"):
                st.session_state.update({f'{tag}_aktiv': False for tag in wochentage_kurz})
        
        st.markdown("---")
        
        # Detaileinstellungen pro Wochentag
        for i, (tag_name, tag_kurz) in enumerate(zip(wochentage_namen, wochentage_kurz)):
            col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 2])
            
            with col1:
                aktiv = st.checkbox(tag_name, 
                                  value=st.session_state.get(f'{tag_kurz}_aktiv', tag_kurz in ['mo', 'di', 'mi', 'do', 'fr']),
                                  key=f'{tag_kurz}_aktiv')
            
            if aktiv:
                with col2:
                    normal_von = st.number_input("Von", 
                                               value=st.session_state.get(f'{tag_kurz}_normal_von', 8), 
                                               min_value=0, max_value=23, key=f'{tag_kurz}_normal_von')
                with col3:
                    normal_bis = st.number_input("Bis", 
                                               value=st.session_state.get(f'{tag_kurz}_normal_bis', 18), 
                                               min_value=1, max_value=24, key=f'{tag_kurz}_normal_bis')
                with col4:
                    absenk_aktiv = st.checkbox("Absenk", 
                                             value=st.session_state.get(f'{tag_kurz}_absenk', True),
                                             key=f'{tag_kurz}_absenk')
                with col5:
                    if normal_bis > normal_von:
                        st.caption(f"Normal: {normal_bis - normal_von}h" + (f", Absenk: {24-(normal_bis - normal_von)}h" if absenk_aktiv else ", Sonst: AUS"))
                    else:
                        st.caption("⚠️ Ungültige Zeiten")
            else:
                normal_von = normal_bis = 0
                absenk_aktiv = False
                with col5:
                    st.caption("❌ AUS")
            
            betriebszeiten[tag_kurz] = {
                'aktiv': aktiv,
                'normal_von': normal_von,
                'normal_bis': normal_bis,
                'absenk_aktiv': absenk_aktiv
            }
        
        st.markdown("---")
        st.markdown("**Ausfallzeiten:**")
        col1, col2 = st.columns(2)
        with col1:
            wartungstage_jahr = st.slider("Wartungstage/Jahr", 0, 50, 10)
        with col2:
            ferien_wochen = st.slider("Betriebsferien [Wochen]", 0, 12, 3)

    # 4. ABSENKPARAMETER
    with st.expander("📉 Absenkbetrieb-Parameter", expanded=True):
        st.markdown("**Was wird abgesenkt?**")
        col1, col2 = st.columns(2)
        with col1:
            temp_absenkung = st.slider("Temperatur-Absenkung [K]", 0, 8, 3)
            vol_absenkung = st.slider("Volumenstrom-Reduktion [%]", 0, 70, 30) / 100
        with col2:
            st.info(f"Absenkbetrieb:\n- Temperatur: -{temp_absenkung}K\n- Volumenstrom: -{vol_absenkung*100:.0f}%")

    # 5. KLIMABEDINGUNGEN (Jahres-Mittelwerte)
    with st.expander("🌤️ Klimabedingungen (Jahres-Ø)", expanded=True):
        st.markdown("**🟢 Außenluftbedingungen:**")
        col1, col2 = st.columns(2)
        with col1:
            temp_aussen = st.slider("Außentemperatur [°C]", -20, 45, 8)
            temp_aussen = st.number_input("Exakt [°C]", value=float(temp_aussen), step=0.5, key="temp_aussen_exact")
        with col2:
            feuchte_aussen = st.slider("Außenluft rel. Feuchte [%]", 30, 90, 65)
            feuchte_aussen = st.number_input("Exakt [%]", value=feuchte_aussen, key="feuchte_aussen_exact")
        
        st.markdown("**🎯 Zuluftbedingungen:**")
        col1, col2 = st.columns(2)
        with col1:
            temp_zuluft = st.slider("Zuluft-Solltemperatur [°C]", 16, 26, 20)
            temp_zuluft = st.number_input("Exakt [°C]", value=float(temp_zuluft), step=0.5, key="temp_zuluft_exact")
        with col2:
            if entfeuchten:
                feuchte_zuluft_soll = st.slider("Zuluft rel. Feuchte [%]", 30, 65, 45)
                feuchte_zuluft_soll = st.number_input("Exakt [%]", value=feuchte_zuluft_soll, key="feuchte_zuluft_exact")
                abs_feuchte_berechnet = abs_feuchte_aus_rel_feuchte(temp_zuluft, feuchte_zuluft_soll) * 1000
                st.caption(f"= {abs_feuchte_berechnet:.2f} g/kg abs.")
            else:
                feuchte_zuluft_soll = feuchte_aussen
                st.info("Keine Entfeuchtung")

    # 6. ERWEITERTE EINGABEN
    with st.expander("📊 Erweiterte Energieberechnung", expanded=True):
        st.markdown("**Thermische Energie (für detaillierte Analyse):**")
        
        eingabe_modus = st.radio("Eingabemodus:", 
                                ["Automatisch berechnen", "Heiz-/Kühlstunden manuell eingeben"])
        
        if eingabe_modus == "Heiz-/Kühlstunden manuell eingeben":
            col1, col2 = st.columns(2)
            with col1:
                heizstunden_jahr = st.number_input("🔴 Heizstunden/Jahr", value=3000, min_value=0, max_value=8760)
                st.caption("Stunden mit aktivem Heizbetrieb")
            with col2:
                kuehlstunden_jahr = st.number_input("🔵 Kühlstunden/Jahr", value=1500, min_value=0, max_value=8760)
                st.caption("Stunden mit aktivem Kühlbetrieb")
        else:
            heizstunden_jahr = None
            kuehlstunden_jahr = None
            st.info("Heiz-/Kühlstunden werden automatisch aus Betriebsprofil berechnet")

    # 7. ENERGIEPREISE
    with st.expander("💰 Energiepreise", expanded=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            preis_strom = st.number_input("⚡ Strom [€/kWh]", value=0.25, format="%.3f")
        with col2:
            preis_waerme = st.number_input("🔴 Fernwärme [€/kWh]", value=0.08, format="%.3f")
        with col3:
            preis_kaelte = st.number_input("🔵 Kälte [€/kWh]", value=0.15, format="%.3f")

with col_right:
    st.header("📊 Ergebnisse & Analyse")
    
    # Berechnungen durchführen
    stunden_jahr = berechne_betriebsstunden(betriebszeiten, wartungstage_jahr, ferien_wochen)
    
    # RLT-Prozesse berechnen
    prozess_normal = berechne_rlt_prozess(temp_aussen, feuchte_aussen, temp_zuluft, feuchte_zuluft_soll, wrg_wirkungsgrad, entfeuchten)
    prozess_absenk = berechne_rlt_prozess(temp_aussen, feuchte_aussen, temp_zuluft - temp_absenkung, feuchte_zuluft_soll, wrg_wirkungsgrad, entfeuchten)
    
    # Leistungsberechnungen
    luftmassenstrom_normal = volumenstrom * 1.2 / 3600
    luftmassenstrom_absenk = volumenstrom * (1 - vol_absenkung) * 1.2 / 3600
    
    # Ventilatorleistungen
    p_ventilator_normal = volumenstrom * sfp * teillast_normal / 1000
    p_ventilator_absenk = volumenstrom * (1 - vol_absenkung) * sfp * teillast_absenk / 1000
    
    # Thermische Leistungen
    p_kuehlung_entf_normal = luftmassenstrom_normal * prozess_normal['leistungen']['kuehlung_entf'] / 1000
    p_nachheizung_normal = luftmassenstrom_normal * prozess_normal['leistungen']['nachheizung'] / 1000
    p_heizung_direkt_normal = luftmassenstrom_normal * prozess_normal['leistungen']['heizung_direkt'] / 1000
    p_kuehlung_direkt_normal = luftmassenstrom_normal * prozess_normal['leistungen']['kuehlung_direkt'] / 1000
    
    p_kuehlung_entf_absenk = luftmassenstrom_absenk * prozess_absenk['leistungen']['kuehlung_entf'] / 1000
    p_nachheizung_absenk = luftmassenstrom_absenk * prozess_absenk['leistungen']['nachheizung'] / 1000
    p_heizung_direkt_absenk = luftmassenstrom_absenk * prozess_absenk['leistungen']['heizung_direkt'] / 1000
    p_kuehlung_direkt_absenk = luftmassenstrom_absenk * prozess_absenk['leistungen']['kuehlung_direkt'] / 1000
    
    # Gesamtleistungen
    p_heizen_normal = p_nachheizung_normal + p_heizung_direkt_normal
    p_kuehlen_normal = p_kuehlung_entf_normal + p_kuehlung_direkt_normal
    p_heizen_absenk = p_nachheizung_absenk + p_heizung_direkt_absenk
    p_kuehlen_absenk = p_kuehlung_entf_absenk + p_kuehlung_direkt_absenk
    
    # Jahresenergie und -kosten
    if heizstunden_jahr and kuehlstunden_jahr:
        # Manuelle Eingabe verwenden
        energie_heizen = p_heizen_normal * heizstunden_jahr
        energie_kuehlen = p_kuehlen_normal * kuehlstunden_jahr
    else:
        # Automatische Berechnung
        energie_heizen = (p_heizen_normal * stunden_jahr['normal'] + p_heizen_absenk * stunden_jahr['absenk'])
        energie_kuehlen = (p_kuehlen_normal * stunden_jahr['normal'] + p_kuehlen_absenk * stunden_jahr['absenk'])
    
    energie_ventilator = (p_ventilator_normal * stunden_jahr['normal'] + p_ventilator_absenk * stunden_jahr['absenk'])
    
    kosten_ventilator = energie_ventilator * preis_strom
    kosten_heizen = energie_heizen * preis_waerme
    kosten_kuehlen = energie_kuehlen * preis_kaelte
    gesamtkosten = kosten_ventilator + kosten_heizen + kosten_kuehlen
    
    # Tab-Struktur mit farbkodierten Ergebnissen
    tab1, tab2, tab3, tab4 = st.tabs(["⚡ Leistungen & Kosten", "🔄 Luftbehandlung", "📈 Diagramme", "📋 Betriebsprofil"])
    
    with tab1:
        st.subheader("⚡ Installierte Leistungen")
        
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.markdown('<div class="metric-ventilator">', unsafe_allow_html=True)
            st.metric("🟡 Ventilator", f"{p_ventilator_normal:.1f} kW",
                     f"Absenk: {p_ventilator_absenk:.1f} kW")
            st.markdown('</div>', unsafe_allow_html=True)
        
        with col2:
            st.markdown('<div class="metric-heizung">', unsafe_allow_html=True)
            st.metric("🔴 Heizung", f"{p_heizen_normal:.1f} kW",
                     f"Nachheizung: {p_nachheizung_normal:.1f} kW" if p_nachheizung_normal > 0.1 else None)
            st.markdown('</div>', unsafe_allow_html=True)
        
        with col3:
           st.markdown('<div class="metric-kuehlung">', unsafe_allow_html=True)
           st.metric("🔵 Kühlung", f"{p_kuehlen_normal:.1f} kW",
                    f"Entfeuchtung: {p_kuehlung_entf_normal:.1f} kW" if p_kuehlung_entf_normal > 0.1 else None)
           st.markdown('</div>', unsafe_allow_html=True)
       
        with col4:
           p_gesamt = p_ventilator_normal + p_heizen_normal + p_kuehlen_normal
           st.metric("⚡ Gesamtleistung", f"{p_gesamt:.1f} kW", "Anschlusswert")
       
       st.markdown("---")
       st.subheader("📊 Jahresenergie & Kosten")
       
       col1, col2, col3, col4 = st.columns(4)
       
       with col1:
           st.markdown('<div class="metric-ventilator">', unsafe_allow_html=True)
           st.metric("🟡 Ventilator", 
                    f"{kosten_ventilator:.0f} €/a",
                    f"{energie_ventilator:.0f} kWh/a")
           st.markdown('</div>', unsafe_allow_html=True)
       
       with col2:
           st.markdown('<div class="metric-heizung">', unsafe_allow_html=True)
           st.metric("🔴 Heizung", 
                    f"{kosten_heizen:.0f} €/a",
                    f"{energie_heizen:.0f} kWh/a")
           st.markdown('</div>', unsafe_allow_html=True)
       
       with col3:
           st.markdown('<div class="metric-kuehlung">', unsafe_allow_html=True)
           st.metric("🔵 Kühlung", 
                    f"{kosten_kuehlen:.0f} €/a",
                    f"{energie_kuehlen:.0f} kWh/a")
           st.markdown('</div>', unsafe_allow_html=True)
       
       with col4:
           st.metric("💰 Gesamtkosten", 
                    f"{gesamtkosten:.0f} €/a",
                    f"{gesamtkosten/12:.0f} €/Monat")
       
       # Erweiterte Kennzahlen
       st.markdown("---")
       st.subheader("📈 Kennzahlen")
       
       col1, col2, col3, col4 = st.columns(4)
       
       with col1:
           if stunden_jahr['gesamt'] > 0:
               spez_kosten = gesamtkosten / (volumenstrom * stunden_jahr['gesamt'] / 1000)
               st.metric("Spez. Kosten", f"{spez_kosten:.3f} €/(m³·h)")
       
       with col2:
           spez_leistung = p_gesamt / volumenstrom * 1000
           st.metric("Spez. Leistung", f"{spez_leistung:.2f} W/m³/h")
       
       with col3:
           jahresenergie_gesamt = (energie_ventilator + energie_heizen + energie_kuehlen) / 1000
           st.metric("Jahresenergie", f"{jahresenergie_gesamt:.1f} MWh/a")
       
       with col4:
           co2_emissionen = (energie_ventilator * 0.4 + energie_heizen * 0.2) / 1000  # t CO2
           st.metric("CO₂-Emissionen", f"{co2_emissionen:.1f} t/a")

   with tab2:
       st.subheader("🔄 Luftbehandlungsprozess")
       
       # Prozess-Tabelle mit Farbcodierung
       df_prozess = pd.DataFrame(prozess_normal['schritte'])
       df_prozess.columns = ['Prozessschritt', 'Temperatur [°C]', 'rel. Feuchte [%]', 'abs. Feuchte [g/kg]', 'Enthalpie [kJ/kg]', 'Taupunkt [°C]']
       
       st.dataframe(df_prozess, use_container_width=True, hide_index=True)
       
       st.markdown("---")
       st.subheader("🔧 Detaillierte Prozessleistungen")
       
       col1, col2 = st.columns(2)
       
       with col1:
           st.markdown("**🔵 Kühlprozesse:**")
           if p_kuehlung_entf_normal > 0.1:
               st.markdown(f'<div class="metric-kuehlung">❄️ <b>Entfeuchtungskühlung:</b> {p_kuehlung_entf_normal:.1f} kW</div>', 
                          unsafe_allow_html=True)
           if p_kuehlung_direkt_normal > 0.1:
               st.markdown(f'<div class="metric-kuehlung">❄️ <b>Direkte Kühlung:</b> {p_kuehlung_direkt_normal:.1f} kW</div>', 
                          unsafe_allow_html=True)
           if p_kuehlung_entf_normal <= 0.1 and p_kuehlung_direkt_normal <= 0.1:
               st.info("Keine Kühlung erforderlich")
       
       with col2:
           st.markdown("**🔴 Heizprozesse:**")
           if p_nachheizung_normal > 0.1:
               st.markdown(f'<div class="metric-heizung">🔥 <b>Nachheizung:</b> {p_nachheizung_normal:.1f} kW</div>', 
                          unsafe_allow_html=True)
           if p_heizung_direkt_normal > 0.1:
               st.markdown(f'<div class="metric-heizung">🔥 <b>Direkte Heizung:</b> {p_heizung_direkt_normal:.1f} kW</div>', 
                          unsafe_allow_html=True)
           if p_nachheizung_normal <= 0.1 and p_heizung_direkt_normal <= 0.1:
               st.info("Keine Heizung erforderlich")
       
       # WRG-Einsparung
       if wrg_wirkungsgrad > 0:
           st.markdown("---")
           st.markdown(f'<div class="metric-wrg">🟣 <b>WRG-Wärmerückgewinnung ({wrg_typ}):</b><br>Wirkungsgrad: {wrg_wirkungsgrad*100:.0f}% - Energieeinsparung durch Vorwärmung/Vorkühlung</div>', 
                      unsafe_allow_html=True)

   with tab3:
       st.subheader("📈 Kosten- & Energieverteilung")
       
       col1, col2 = st.columns(2)
       
       with col1:
           # Kostenverteilung mit Farbschema
           kosten_data = []
           labels_data = []
           colors_data = []
           
           if kosten_ventilator > 0:
               kosten_data.append(kosten_ventilator)
               labels_data.append("🟡 Ventilator")
               colors_data.append(FARBEN['ventilator'])
           
           if kosten_heizen > 0:
               kosten_data.append(kosten_heizen)
               labels_data.append("🔴 Heizung")
               colors_data.append(FARBEN['heizung'])
           
           if kosten_kuehlen > 0:
               kosten_data.append(kosten_kuehlen)
               labels_data.append("🔵 Kühlung")
               colors_data.append(FARBEN['kuehlung'])
           
           if kosten_data:
               fig_pie = go.Figure(data=[go.Pie(
                   labels=labels_data,
                   values=kosten_data,
                   hole=0.4,
                   marker=dict(colors=colors_data),
                   textinfo='label+percent+value',
                   texttemplate='%{label}<br>%{percent}<br>%{value:.0f}€'
               )])
               fig_pie.update_layout(
                   title="Jahreskosten-Verteilung",
                   height=400,
                   showlegend=False
               )
               st.plotly_chart(fig_pie, use_container_width=True)
       
       with col2:
           # Leistungsverteilung
           leistungen_data = []
           leistungen_labels = []
           leistungen_colors = []
           
           if p_ventilator_normal > 0:
               leistungen_data.append(p_ventilator_normal)
               leistungen_labels.append("🟡 Ventilator")
               leistungen_colors.append(FARBEN['ventilator'])
           
           if p_heizen_normal > 0:
               leistungen_data.append(p_heizen_normal)
               leistungen_labels.append("🔴 Heizung")
               leistungen_colors.append(FARBEN['heizung'])
           
           if p_kuehlen_normal > 0:
               leistungen_data.append(p_kuehlen_normal)
               leistungen_labels.append("🔵 Kühlung")
               leistungen_colors.append(FARBEN['kuehlung'])
           
           if leistungen_data:
               fig_bar = go.Figure(data=[go.Bar(
                   x=leistungen_labels,
                   y=leistungen_data,
                   marker_color=leistungen_colors,
                   text=[f"{val:.1f} kW" for val in leistungen_data],
                   textposition='auto'
               )])
               fig_bar.update_layout(
                   title="Installierte Leistungen",
                   height=400,
                   yaxis_title="Leistung [kW]",
                   showlegend=False
               )
               st.plotly_chart(fig_bar, use_container_width=True)
       
       # Energiebilanz-Diagramm
       st.markdown("---")
       st.subheader("📊 Jahresenergiebilanz")
       
       energien = [energie_ventilator/1000, energie_heizen/1000, energie_kuehlen/1000]  # in MWh
       energie_labels = ["🟡 Ventilator", "🔴 Heizung", "🔵 Kühlung"]
       energie_colors = [FARBEN['ventilator'], FARBEN['heizung'], FARBEN['kuehlung']]
       
       fig_energie = go.Figure(data=[go.Bar(
           x=energie_labels,
           y=energien,
           marker_color=energie_colors,
           text=[f"{val:.1f} MWh" for val in energien],
           textposition='auto'
       )])
       fig_energie.update_layout(
           title="Jahresenergieverbrauch",
           height=350,
           yaxis_title="Energie [MWh/Jahr]",
           showlegend=False
       )
       st.plotly_chart(fig_energie, use_container_width=True)

   with tab4:
       st.subheader("📋 Detailliertes Betriebsprofil")
       
       # Wochenprofil-Übersicht
       st.markdown("**Wochentagsprofil:**")
       
       betriebsprofil_data = []
       for tag_kurz, tag_name in zip(['mo', 'di', 'mi', 'do', 'fr', 'sa', 'so'], 
                                    ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']):
           if betriebszeiten[tag_kurz]['aktiv']:
               normal_stunden = betriebszeiten[tag_kurz]['normal_bis'] - betriebszeiten[tag_kurz]['normal_von']
               absenk_stunden = (24 - normal_stunden) if betriebszeiten[tag_kurz]['absenk_aktiv'] else 0
               aus_stunden = 24 - normal_stunden - absenk_stunden
           else:
               normal_stunden = absenk_stunden = 0
               aus_stunden = 24
           
           betriebsprofil_data.append({
               'Wochentag': tag_name,
               'Normal [h]': normal_stunden,
               'Absenk [h]': absenk_stunden,
               'AUS [h]': aus_stunden,
               'Normal-Zeit': f"{betriebszeiten[tag_kurz]['normal_von']:02d}:00-{betriebszeiten[tag_kurz]['normal_bis']:02d}:00" if betriebszeiten[tag_kurz]['aktiv'] else "-"
           })
       
       df_betrieb = pd.DataFrame(betriebsprofil_data)
       st.dataframe(df_betrieb, use_container_width=True, hide_index=True)
       
       # Jahres-Betriebsstunden-Übersicht
       st.markdown("---")
       st.subheader("⏰ Jahres-Betriebsstunden-Bilanz")
       
       col1, col2, col3, col4 = st.columns(4)
       
       with col1:
           st.metric("🟢 Normalbetrieb", f"{stunden_jahr['normal']:,.0f} h/a")
           st.caption(f"= {stunden_jahr['normal']/24:.0f} Tage")
       
       with col2:
           st.metric("🟡 Absenkbetrieb", f"{stunden_jahr['absenk']:,.0f} h/a")
           st.caption(f"= {stunden_jahr['absenk']/24:.0f} Tage")
       
       with col3:
           st.metric("🔴 AUS-Zeiten", f"{stunden_jahr['aus']:,.0f} h/a")
           st.caption(f"= {stunden_jahr['aus']/24:.0f} Tage")
       
       with col4:
           st.metric("⚡ Aktive Zeit", f"{stunden_jahr['gesamt']:,.0f} h/a")
           st.caption(f"= {stunden_jahr['gesamt']/8760*100:.1f}% des Jahres")
       
       # Betriebsprofil-Visualisierung
       st.markdown("---")
       st.subheader("📊 Wochenprofil-Visualisierung")
       
       wochentage_viz = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']
       normal_stunden_viz = [df_betrieb.iloc[i]['Normal [h]'] for i in range(7)]
       absenk_stunden_viz = [df_betrieb.iloc[i]['Absenk [h]'] for i in range(7)]
       aus_stunden_viz = [df_betrieb.iloc[i]['AUS [h]'] for i in range(7)]
       
       fig_woche = go.Figure()
       
       fig_woche.add_trace(go.Bar(
           x=wochentage_viz,
           y=normal_stunden_viz,
           name='🟢 Normalbetrieb',
           marker_color='#27AE60'
       ))
       
       fig_woche.add_trace(go.Bar(
           x=wochentage_viz,
           y=absenk_stunden_viz,
           name='🟡 Absenkbetrieb',
           marker_color='#F39C12'
       ))
       
       fig_woche.add_trace(go.Bar(
           x=wochentage_viz,
           y=aus_stunden_viz,
           name='🔴 AUS',
           marker_color='#E74C3C'
       ))
       
       fig_woche.update_layout(
           title="Wochenprofil: Betriebsmodi pro Tag",
           barmode='stack',
           height=400,
           yaxis_title="Stunden pro Tag",
           xaxis_title="Wochentag"
       )
       st.plotly_chart(fig_woche, use_container_width=True)

# Footer mit Professional PDF Export
st.markdown("---")
col1, col2, col3 = st.columns([2, 1, 2])

with col2:
   if st.button("📄 Professional Report", type="primary", use_container_width=True):
       buffer = BytesIO()
       p = canvas.Canvas(buffer, pagesize=A4)
       width, height = A4
       
       # Header
       p.setFont("Helvetica-Bold", 18)
       p.drawString(50, height-50, "RLT-Kostenanalyse Professional v4.0")
       p.setFont("Helvetica", 10)
       p.drawString(50, height-75, f"Erstellt: {pd.Timestamp.now().strftime('%d.%m.%Y %H:%M')}")
       
       y = height - 120
       
       # Anlagendaten
       p.setFont("Helvetica-Bold", 14)
       p.drawString(50, y, "Anlagendaten")
       y -= 25
       
       p.setFont("Helvetica", 11)
       anlagendaten = [
           f"Volumenstrom: {volumenstrom:,.0f} m³/h",
           f"SFP: {sfp:.1f} W/(m³/h)",
           f"Teillast Normal/Absenk: {teillast_normal*100:.0f}%/{teillast_absenk*100:.0f}%",
           f"WRG: {wrg_typ} ({wrg_wirkungsgrad*100:.0f}%)" if wrg_vorhanden else "Keine WRG",
           ""
       ]
       
       for item in anlagendaten:
           p.drawString(70, y, item)
           y -= 18
       
       # Klimabedingungen
       p.setFont("Helvetica-Bold", 14)
       p.drawString(50, y, "Klimabedingungen (Jahres-Ø)")
       y -= 25
       
       p.setFont("Helvetica", 11)
       klimadaten = [
           f"Außenluft: {temp_aussen:.1f}°C, {feuchte_aussen:.1f}% rF",
           f"Zuluft: {temp_zuluft:.1f}°C" + (f", {feuchte_zuluft_soll:.1f}% rF" if entfeuchten else ""),
           f"Entfeuchtung: {'Aktiv' if entfeuchten else 'Inaktiv'}",
           f"Absenkung: -{temp_absenkung}K, -{vol_absenkung*100:.0f}% Volumenstrom",
           ""
       ]
       
       for item in klimadaten:
           p.drawString(70, y, item)
           y -= 18
       
       # Betriebszeiten
       p.setFont("Helvetica-Bold", 14)
       p.drawString(50, y, "Betriebszeiten")
       y -= 25
       
       p.setFont("Helvetica", 11)
       betriebsdaten = [
           f"Normalbetrieb: {stunden_jahr['normal']:,.0f} h/Jahr",
           f"Absenkbetrieb: {stunden_jahr['absenk']:,.0f} h/Jahr",
           f"Wartung/Ferien: {wartungstage_jahr} Tage + {ferien_wochen} Wochen",
           f"Aktivzeit gesamt: {stunden_jahr['gesamt']:,.0f} h/Jahr ({stunden_jahr['gesamt']/8760*100:.1f}%)",
           ""
       ]
       
       for item in betriebsdaten:
           p.drawString(70, y, item)
           y -= 18
       
       # Leistungen
       p.setFont("Helvetica-Bold", 14)
       p.drawString(50, y, "Installierte Leistungen")
       y -= 25
       
       p.setFont("Helvetica", 11)
       leistungsdaten = [
           f"Ventilator: {p_ventilator_normal:.1f} kW (Absenk: {p_ventilator_absenk:.1f} kW)",
           f"Heizung: {p_heizen_normal:.1f} kW" + (f" (Nachheizung: {p_nachheizung_normal:.1f} kW)" if p_nachheizung_normal > 0.1 else ""),
           f"Kühlung: {p_kuehlen_normal:.1f} kW" + (f" (Entfeuchtung: {p_kuehlung_entf_normal:.1f} kW)" if p_kuehlung_entf_normal > 0.1 else ""),
           f"Anschlusswert: {p_gesamt:.1f} kW",
           ""
       ]
       
       for item in leistungsdaten:
           p.drawString(70, y, item)
           y -= 18
       
       # Jahresenergie und Kosten
       p.setFont("Helvetica-Bold", 14)
       p.drawString(50, y, "Jahresenergie und Kosten")
       y -= 25
       
       p.setFont("Helvetica", 11)
       kostendaten = [
           f"Ventilator: {energie_ventilator:.0f} kWh/a → {kosten_ventilator:,.0f} €/Jahr",
           f"Heizung: {energie_heizen:.0f} kWh/a → {kosten_heizen:,.0f} €/Jahr",
           f"Kühlung: {energie_kuehlen:.0f} kWh/a → {kosten_kuehlen:,.0f} €/Jahr",
           "",
           f"GESAMTKOSTEN: {gesamtkosten:,.0f} €/Jahr ({gesamtkosten/12:,.0f} €/Monat)",
           f"Gesamtenergie: {jahresenergie_gesamt:.1f} MWh/Jahr"
       ]
       
       for item in kostendaten:
           if "GESAMTKOSTEN" in item:
               p.setFont("Helvetica-Bold", 12)
           p.drawString(70, y, item)
           p.setFont("Helvetica", 11)
           y -= 18
       
       # Fußzeile
       p.setFont("Helvetica-Italic", 8)
       p.drawString(50, 50, "RLT-Kostenanalyse Professional v4.0 - Fachlich korrekte Berechnung")
       p.drawString(50, 35, f"Wochentagsbasierte Betriebszeiten, Magnus-Formel, h,x-Diagramm")
       
       p.save()
       buffer.seek(0)
       
       st.download_button(
           label="📥 Professional PDF Report",
           data=buffer,
           file_name=f"RLT_Professional_v4_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.pdf",
           mime="application/pdf",
           use_container_width=True
       )

st.markdown("---")
st.markdown("*RLT-Kostenanalyse Professional v4.0 | Fachlich korrekte Berechnungen mit professioneller Benutzerführung für Techniker und kaufmännisches Personal*")
