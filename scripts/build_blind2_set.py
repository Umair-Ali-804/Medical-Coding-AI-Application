"""Final blind ICD-10-CM set (18 synthetic notes), written AFTER all rule work and run exactly once.
Mostly conditions the guideline rules do not target, to measure how the system generalizes.
Every gold code was checked against the FY2026 CDC order file (exists, billable).

    python scripts/build_blind2_set.py   ->  data/evaluation/blind2.jsonl
"""
import json
from pathlib import Path

CASES = [
    ("C01", "outpatient", "M", 54, "CLINIC NOTE\n54 y/o man with sudden severe pain, redness and swelling of the right great toe since last night. Uric acid 9.1.\nAssessment: Acute gout flare, right great toe.\nPlan: Colchicine, NSAID, follow up.", ["M10.071"], []),
    ("C02", "outpatient", "F", 29, "CLINIC NOTE\n29 y/o woman with dysuria and urgency for 1 day. No blood in urine, no fever, no flank pain.\nUA: positive nitrites.\nAssessment: Acute cystitis without hematuria.\nPlan: Nitrofurantoin x 5 days.", ["N30.00"], []),
    ("C03", "outpatient", "M", 8, "PEDIATRIC VISIT\n8 y/o boy with left ear pain and fever for 2 days. Left tympanic membrane bulging and erythematous. Right ear normal.\nAssessment: Acute otitis media, left ear.\nPlan: Amoxicillin.", ["H66.92"], []),
    ("C04", "outpatient", "F", 45, "NEUROLOGY\n45 y/o woman with episodic unilateral throbbing headaches with nausea, no aura, 3-4 per month, respond to triptans.\nAssessment: Migraine without aura, not intractable, without status migrainosus.\nPlan: Continue sumatriptan.", ["G43.009"], []),
    ("C05", "inpatient", "M", 70, "DISCHARGE SUMMARY\n70 y/o man with COPD on home oxygen 2 L admitted with increased dyspnea and sputum. ABG pO2 52 on room air.\nCurrent smoker, 1 pack of cigarettes per day.\nDischarge diagnoses:\n1. COPD with acute exacerbation\n2. Acute on chronic hypoxic respiratory failure\n3. Nicotine dependence, cigarettes", ["J44.1", "J96.21", "F17.210"], ["Z99.81"]),
    ("C06", "outpatient", "F", 62, "PRIMARY CARE\n62 y/o woman with essential hypertension and type 2 diabetes. Claudication with walking. ABI 0.7 bilaterally, no ulcers, no gangrene.\nMedications: glipizide 5 mg daily, amlodipine.\nAssessment:\n1. Type 2 diabetes with diabetic peripheral angiopathy without gangrene\n2. Essential hypertension", ["E11.51", "I10", "Z79.84"], []),
    ("C07", "outpatient", "M", 35, "BEHAVIORAL HEALTH\n35 y/o man with excessive worry most days for 8 months, restlessness, poor concentration. Also trouble falling asleep.\nAssessment:\n1. Generalized anxiety disorder\n2. Insomnia\nPlan: Start sertraline, CBT referral.", ["F41.1", "G47.00"], []),
    ("C08", "outpatient", "F", 50, "HEMATOLOGY\n50 y/o woman with fatigue. Hgb 9.2, ferritin 6. Heavy regular menstrual periods for a year.\nAssessment: Iron deficiency anemia secondary to chronic blood loss from menorrhagia (excessive menstruation with regular cycle).\nPlan: IV iron, gynecology referral.", ["D50.0", "N92.0"], []),
    ("C09", "ED", "M", 40, "EMERGENCY DEPARTMENT\n40 y/o man rolled his right ankle stepping off a curb. Swelling over the anterior talofibular ligament. X-ray negative for fracture.\nAssessment: Sprain of anterior talofibular ligament of right ankle, initial encounter.\nPlan: RICE, brace.", ["S93.491A"], ["W10.1XXA", "X50.0XXA", "Y92.410", "Y92.480"]),
    ("C10", "inpatient", "F", 75, "DISCHARGE SUMMARY\n75 y/o woman admitted with fever and left flank pain. Urine and blood cultures grew E. coli. Sodium 127.\nDischarge diagnoses:\n1. Acute pyelonephritis due to E. coli\n2. Hyponatremia", ["N10", "B96.20", "E87.1"], []),
    ("C11", "outpatient", "M", 60, "UROLOGY\n60 y/o man with nocturia 3 times nightly and weak stream. Prostate enlarged, smooth.\nAssessment: Benign prostatic hyperplasia with lower urinary tract symptoms, nocturia.\nPlan: Tamsulosin.", ["N40.1", "R35.1"], []),
    ("C12", "outpatient", "F", 66, "CLINIC NOTE\n66 y/o postmenopausal woman, DEXA T-score -2.8 at the hip. No fractures.\nMedications: alendronate 70 mg weekly for 2 years.\nAssessment: Postmenopausal osteoporosis without current pathological fracture.\nPlan: Continue alendronate, calcium and vitamin D.", ["M81.0", "Z79.83"], []),
    ("C13", "outpatient", "F", 31, "CLINIC NOTE\n31 y/o woman with fatigue. 25-OH vitamin D level 14.\nAssessment: Vitamin D deficiency.\nPlan: Ergocalciferol 50,000 units weekly.", ["E55.9"], []),
    ("C14", "outpatient", "M", 57, "HEPATOLOGY\n57 y/o man with chronic hepatitis C, genotype 1a, compensated, no hepatic coma or encephalopathy.\nAssessment: Chronic viral hepatitis C.\nPlan: Start direct-acting antiviral therapy.", ["B18.2"], []),
    ("C15", "inpatient", "M", 68, "DISCHARGE SUMMARY\n68 y/o man with hypertension and chronic kidney disease stage 5, not yet on dialysis, admitted with dyspnea and edema. EF 35% with impaired relaxation.\nDischarge diagnoses:\n1. Acute on chronic combined systolic and diastolic heart failure\n2. Hypertension\n3. Chronic kidney disease stage 5", ["I13.2", "I50.43", "N18.5"], []),
    ("C16", "outpatient", "F", 72, "GERIATRIC CLINIC\n72 y/o woman with type 2 diabetes, glucose readings 250-300, on insulin glargine long-term. She has fallen twice this year.\nAssessment:\n1. Type 2 diabetes mellitus with hyperglycemia\n2. History of falling\nPlan: Adjust insulin, home safety evaluation.", ["E11.65", "Z79.4", "Z91.81"], []),
    ("C17", "outpatient", "F", 24, "URGENT CARE\n24 y/o woman fell while jogging and struck her left knee. Bruising and tenderness, full range of motion, x-ray normal.\nAssessment: Contusion of left knee, initial encounter.\nPlan: Ice, NSAIDs.", ["S80.02XA"], ["W01.0XXA", "W18.30XA", "W18.39XA", "W19.XXXA", "Y93.02", "Y92.480"]),
    ("C18", "outpatient", "M", 49, "SLEEP REFERRAL\n49 y/o man with loud snoring and daytime somnolence. Wife reports pauses in breathing.\nAssessment: Snoring and daytime somnolence, rule out obstructive sleep apnea.\nPlan: Polysomnography.", ["R06.83", "R40.0"], []),
]


def main() -> None:
    out = Path(__file__).resolve().parents[1] / "data" / "evaluation" / "blind2.jsonl"
    with out.open("w") as fh:
        for cid, enc, sex, age, note, gold, optional in CASES:
            fh.write(json.dumps({"document_id": cid, "clinical_text": note,
                                 "encounter_type": "outpatient" if enc == "ED" else enc, "patient_sex": sex,
                                 "patient_age": age, "gold_codes": [{"system": "ICD-10-CM", "code": c} for c in gold],
                                 "optional_codes": optional, "synthetic": True}) + "\n")
    print(f"wrote {len(CASES)} cases to {out}")


if __name__ == "__main__":
    main()
