#!/usr/bin/env python3
"""
Extractor de datos personales con BiLSTM-CRF (RNN) en PyTorch.

Campos: primer_nombre, segundo_nombre, primer_apellido, segundo_apellido,
        tipo_documento, numero_documento, email, telefono

Uso:
  python ner_documentos.py gen   --n 20 --out muestra.jsonl        # ver datos sinteticos
  python ner_documentos.py train --synth 8000 --epochs 12 --out modelo.pt
  python ner_documentos.py train --real mis_datos.jsonl --out modelo.pt
  python ner_documentos.py predict --model modelo.pt archivo.pdf   # pdf, docx, png, jpg, html, md, url

Formato JSONL de datos reales anotados (una linea por documento):
  {"tokens": ["Juan", "Perez", ...], "tags": ["B-PRIMER_NOMBRE", "B-PRIMER_APELLIDO", ...]}
  (tokeniza con la funcion tokenize() de este archivo para que coincida con el modelo)
"""
import argparse
import json
import random
import re
import string
import sys
import unicodedata
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. Etiquetas y tokenizacion
# ---------------------------------------------------------------------------
VERSION = "v5-lectura-pdfplumber-persona-unica"  # para confirmar que se ejecuta el codigo nuevo

ENTITIES = ["PRIMER_NOMBRE", "SEGUNDO_NOMBRE", "PRIMER_APELLIDO", "SEGUNDO_APELLIDO",
            "TIPO_DOC", "NUM_DOC", "EMAIL", "TELEFONO"]
TAGS = ["O"] + [f"{p}-{e}" for e in ENTITIES for p in ("B", "I")]
TAG2ID = {t: i for i, t in enumerate(TAGS)}

# Orden importa: salto de linea, email, numeros (1.234.567 / 300-123-4567 / +57), palabras, simbolos
TOKEN_RE = re.compile(
    r"\n"
    r"|[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
    r"|\+?\d+(?:[-.]\d+)*"
    r"|\w+"
    r"|[^\w\s]",
    re.UNICODE,
)


def tokenize(text):
    """Devuelve [(token, inicio, fin)]. Los saltos de linea se conservan (dan pistas de layout)."""
    out = []
    for m in TOKEN_RE.finditer(text):
        tok = m.group()
        if tok == "\n" and out and out[-1][0] == "\n":
            continue
        out.append((tok, m.start(), m.end()))
    return out


SHAPES = ["<pad>", "NL", "EMAIL", "DIGS", "DIGM", "DIGL", "NUMMIX",
          "UPPER", "LOWER", "TITLE", "MIXED", "PUNCT"]
SHAPE2ID = {s: i for i, s in enumerate(SHAPES)}


def shape_of(tok):
    if tok == "\n":
        return "NL"
    if "@" in tok:
        return "EMAIL"
    digits = sum(ch.isdigit() for ch in tok)
    if digits == len(tok):
        return "DIGS" if len(tok) < 4 else "DIGM" if len(tok) < 8 else "DIGL"
    if digits:
        return "NUMMIX"
    if not any(ch.isalpha() for ch in tok):
        return "PUNCT"
    if tok.isupper():
        return "UPPER"
    if tok.islower():
        return "LOWER"
    if tok.istitle():
        return "TITLE"
    return "MIXED"


def norm_word(tok):
    if tok == "\n":
        return "<nl>"
    if "@" in tok:
        return "<email>"
    return re.sub(r"\d", "0", tok.lower())


# ---------------------------------------------------------------------------
# 2. Generador de datos sinteticos (Faker es_CO + plantillas desordenadas)
# ---------------------------------------------------------------------------
_fake = None


def fake():
    global _fake
    if _fake is None:
        from faker import Faker
        _fake = Faker("es_CO")
    return _fake


DOC_TYPES = ["CC", "C.C.", "TI", "CE", "C.E.", "PA", "PAS", "Cédula", "Cédula de Ciudadanía",
             "Cédula de Extranjería", "Pasaporte", "Tarjeta de Identidad", "cc", "T.I."]
DOMAINS = ["gmail.com", "hotmail.com", "outlook.com", "yahoo.es", "empresa.com.co",
           "unal.edu.co", "correo.com", "misena.edu.co", "live.com"]
PARTICLES = ["de", "del", "de la", "de los", "de las"]
PARTICLE_SET = {"de", "del", "la", "los", "las", "y"}

L_NOMBRES = ["Nombres", "Nombre", "Primer nombre y segundo nombre"]
L_APELL = ["Apellidos", "Apellido", "Primer y segundo apellido"]
L_TIPO = ["Tipo de documento", "Tipo documento", "Tipo de identificación", "Tipo ID", "Documento"]
L_NUM = ["Número de documento", "No. documento", "Número", "Nro. de identificación",
         "Identificación", "N° documento"]
L_MAIL = ["Correo electrónico", "Email", "E-mail", "Correo", "Mail"]
L_TEL = ["Teléfono", "Celular", "Tel.", "Móvil", "Contacto", "Teléfono de contacto"]

NARRATIVAS = [
    "Yo, {FULL}, identificado(a) con {TD} número {ND}, me permito informar que pueden contactarme al correo {EM} o al teléfono {TEL}.",
    "Por medio de la presente, {FULL} ({TD} {ND}) autoriza el tratamiento de sus datos personales. Contacto: {TEL} / {EM}.",
    "Hoja de vida de {FULL_INV}. {TD}: {ND}. Celular {TEL}. Correo: {EM}",
    "El/La señor(a) {FULL} portador(a) de la {TD} No. {ND} reside en Bogotá. Teléfono de contacto {TEL}, email {EM}.",
    "CERTIFICA que {FULL_INV}, con {TD} {ND}, ha laborado en nuestra empresa. Para verificación: {EM}, {TEL}.",
    "{EM} - {TEL}\n\nAsunto: Solicitud\n\nCordial saludo, mi nombre es {FULL} y mi documento es {TD} {ND}.",
]
SNIP_NAME = ["Nombre: {FULL}", "Apellidos y nombres: {FULL_INV}", "Solicitante: {FULL}",
             "Titular: {FULL_INV}", "Atentamente,\n{FULL}", "Señor(a) {FULL}"]
SNIP_DOC = ["{TD} {ND}", "Identificación: {TD} {ND}", "Documento de identidad: {TD} No. {ND}",
            "{TD}: {ND}"]
SNIP_CONTACT = ["Email: {EM}", "Tel: {TEL}", "Contacto: {TEL} | {EM}", "{EM}",
                "Celular {TEL} - Correo {EM}", "{TEL}"]


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def gen_phone():
    base = f"3{random.randint(0, 2)}{random.randint(0, 9)}{random.randint(1000000, 9999999)}"
    return random.choice([base, f"{base[:3]} {base[3:6]} {base[6:]}", f"{base[:3]}-{base[3:6]}-{base[6:]}",
                          f"+57 {base[:3]} {base[3:6]} {base[6:]}", f"+57{base}"])


def gen_email(n1, a1, a2=None):
    def slug(s):
        return re.sub(r"[^a-z0-9]", "", strip_accents(s).lower())
    local = random.choice([
        f"{slug(n1)}.{slug(a1)}", f"{slug(n1)[0]}{slug(a1)}{random.randint(1, 99)}",
        f"{slug(n1)}{slug(a1)[0]}{random.randint(1970, 2005)}", f"{slug(n1)}_{slug(a1)}",
        f"{slug(n1)}{slug(a2 or 'x')}"])
    return f"{local}@{random.choice(DOMAINS)}"


def gen_person():
    f = fake()
    n1 = f.first_name().split()[0]
    n2 = f.first_name().split()[0] if random.random() < 0.55 else None
    a1 = f.last_name()
    if random.random() < 0.08:
        a1 = f"{random.choice(PARTICLES).capitalize()} {a1}"
    a2 = f.last_name() if random.random() < 0.9 else None

    td = random.choice(DOC_TYPES)
    if td.lower().startswith("pa"):
        nd = random.choice(string.ascii_uppercase) + random.choice(string.ascii_uppercase) \
             + str(random.randint(100000, 999999))
    else:
        n = random.randint(100000, 1999999999)
        nd = f"{n:,}".replace(",", ".") if random.random() < 0.3 else str(n)

    tel = gen_phone()
    em = gen_email(n1, a1, a2)
    return dict(n1=n1, n2=n2, a1=a1, a2=a2, td=td, nd=nd, tel=tel, em=em)


def name_pieces(p, inverted=False, upper=False):
    names = [(p["n1"], "PRIMER_NOMBRE")] + ([(p["n2"], "SEGUNDO_NOMBRE")] if p["n2"] else [])
    surs = [(p["a1"], "PRIMER_APELLIDO")] + ([(p["a2"], "SEGUNDO_APELLIDO")] if p["a2"] else [])
    seq = surs + names if inverted else names + surs
    out = []
    for i, x in enumerate(seq):
        if i:
            out.append((" ", None))
        out.append((x[0].upper(), x[1]) if upper else x)
    return out


PH = re.compile(r"\{(FULL_INV|FULL|TD|ND|EM|TEL)\}")


def fill(template, p):
    pieces, pos = [], 0
    for m in PH.finditer(template):
        if m.start() > pos:
            pieces.append((template[pos:m.start()], None))
        k = m.group(1)
        if k == "FULL":
            pieces += name_pieces(p)
        elif k == "FULL_INV":
            pieces += name_pieces(p, True)
        elif k == "TD":
            pieces.append((p["td"], "TIPO_DOC"))
        elif k == "ND":
            pieces.append((p["nd"], "NUM_DOC"))
        elif k == "EM":
            pieces.append((p["em"], "EMAIL"))
        elif k == "TEL":
            pieces.append((p["tel"], "TELEFONO"))
        pos = m.end()
    if pos < len(template):
        pieces.append((template[pos:], None))
    return pieces


def noise_line():
    f = fake()
    opciones = [
        lambda: f"Fecha: {f.date()}",
        lambda: "Dirección: " + f.address().replace("\n", ", "),
        lambda: f.sentence(nb_words=random.randint(6, 15)),
        lambda: f"Radicado No. {random.randint(10000, 99999999)}",
        lambda: f"Página {random.randint(1, 5)} de {random.randint(5, 9)}",
        lambda: f"NIT {random.randint(800000000, 900999999)}-{random.randint(0, 9)}",
        lambda: f"Ciudad: {f.city()}",
        lambda: ("Valor: $" + f"{random.randint(1000, 9999999):,}").replace(",", "."),
        lambda: f.company(),
        lambda: f"PBX: 60{random.randint(1, 8)} {random.randint(100, 999)} {random.randint(1000, 9999)}",
        lambda: f"Atención al cliente: servicioalcliente@{random.choice(DOMAINS)}",
        lambda: f"Código postal {random.randint(100000, 999999)}",
        lambda: f"Colegio {f.first_name().split()[0]} {f.last_name()} {f.last_name()}",
        lambda: f"Institución Educativa {f.first_name().split()[0]} {f.last_name()}",
        lambda: f"Fundación {f.first_name().split()[0]} {f.last_name()}",
        lambda: f"Clínica {f.last_name()} {f.last_name()}",
        lambda: f"Referencia: {f.first_name().split()[0]} {f.last_name()} {f.last_name()} Tel. {gen_phone()}",
        lambda: f"Atendido por {f.first_name().split()[0]} {f.last_name()}, "
                f"{gen_email(f.first_name().split()[0], f.last_name())}",
    ]
    return random.choice(opciones)()


def join_lines(lines, sep="\n"):
    out = []
    for i, ln in enumerate(lines):
        if i:
            out.append((sep, None))
        out += ln
    return out


def doc_form(p):
    sep = random.choice([": ", " : ", ":\t", " - "])
    style = random.choice(["plain", "bullets", "table"])

    def blk(labels, value):
        lab = random.choice(labels)
        head = {"bullets": f"- **{lab}**{sep}", "table": f"| {lab} | "}.get(style, lab + sep)
        return [(head, None)] + value + ([(" |", None)] if style == "table" else [])

    mode = random.choice(["grouped", "separate", "full", "full_inv"])
    blocks = []
    if mode == "grouped":
        nm = [(p["n1"], "PRIMER_NOMBRE")] + ([(" ", None), (p["n2"], "SEGUNDO_NOMBRE")] if p["n2"] else [])
        ap = [(p["a1"], "PRIMER_APELLIDO")] + ([(" ", None), (p["a2"], "SEGUNDO_APELLIDO")] if p["a2"] else [])
        blocks += [blk(L_NOMBRES, nm), blk(L_APELL, ap)]
    elif mode == "separate":
        blocks.append(blk(["Primer nombre"], [(p["n1"], "PRIMER_NOMBRE")]))
        if p["n2"]:
            blocks.append(blk(["Segundo nombre"], [(p["n2"], "SEGUNDO_NOMBRE")]))
        blocks.append(blk(["Primer apellido"], [(p["a1"], "PRIMER_APELLIDO")]))
        if p["a2"]:
            blocks.append(blk(["Segundo apellido"], [(p["a2"], "SEGUNDO_APELLIDO")]))
    elif mode == "full":
        blocks.append(blk(["Nombre completo", "Nombre", "Nombres y apellidos"], name_pieces(p)))
    else:
        blocks.append(blk(["Apellidos y nombres", "Titular"], name_pieces(p, True)))

    if random.random() < 0.4:
        blocks.append(blk(["Identificación", "Documento", "Documento de identidad"],
                          [(p["td"], "TIPO_DOC"), (" ", None), (p["nd"], "NUM_DOC")]))
    else:
        blocks += [blk(L_TIPO, [(p["td"], "TIPO_DOC")]), blk(L_NUM, [(p["nd"], "NUM_DOC")])]
    if random.random() < 0.9:
        blocks.append(blk(L_MAIL, [(p["em"], "EMAIL")]))
    if random.random() < 0.9:
        blocks.append(blk(L_TEL, [(p["tel"], "TELEFONO")]))
    if random.random() < 0.5:
        random.shuffle(blocks)

    lines = [] if style != "table" else [[("| Campo | Valor |", None)], [("|---|---|", None)]]
    for b in blocks:
        for _ in range(random.choice([0, 0, 1, 2])):
            n = noise_line()
            lines.append([(f"| Nota | {n} |" if style == "table" else n, None)])
        lines.append(b)
    for _ in range(random.randint(0, 3)):
        lines.append([(noise_line(), None)])
    return join_lines(lines)


def doc_narrative(p):
    lines = [[(noise_line(), None)] for _ in range(random.randint(0, 3))]
    lines.append(fill(random.choice(NARRATIVAS), p))
    lines += [[(noise_line(), None)] for _ in range(random.randint(0, 3))]
    return join_lines(lines, random.choice(["\n", "\n\n"]))


def doc_scatter(p):
    """Datos en distintas partes del documento, mezclados con ruido."""
    parts = [fill(random.choice(SNIP_NAME), p), fill(random.choice(SNIP_DOC), p),
             fill(random.choice(SNIP_CONTACT), p)]
    random.shuffle(parts)
    lines = []
    for part in parts:
        for _ in range(random.randint(1, 4)):
            lines.append([(noise_line(), None)])
        lines.append(part)
    for _ in range(random.randint(0, 3)):
        lines.append([(noise_line(), None)])
    return join_lines(lines, random.choice(["\n", "\n\n"]))


PROFESIONES = ["Tecnólogo en Análisis y Desarrollo de Software | Estudiante de Ingeniería de Software",
               "Ingeniera Civil", "Contador Público", "Auxiliar Administrativa", "Desarrollador Full Stack",
               "Abogada especialista en derecho laboral", "Técnico en Sistemas",
               "Licenciada en Educación Infantil", "Administrador de Empresas", "Diseñadora Gráfica"]
CARGOS = ["Analista de Calidad de Software", "Auxiliar de apoyo en gestión documental", "Desarrolladora Backend",
          "Contador", "Ingeniero de Proyectos", "Asistente administrativa", "Docente de matemáticas",
          "Coordinadora de ventas", "Laboratorista", "Profesional Calidad Académica"]
MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
CENTROS = ["Sena", "Universidad Nacional de Colombia", "Fundación Universitaria del Área Andina",
           "Corporación Universitaria Minuto de Dios", "Universidad Distrital", "Politécnico Grancolombiano"]
TITULOS = ["Técnico en programación de software", "Bachiller académico", "Profesional en ingeniería de software",
           "Tecnólogo en gestión administrativa", "Contaduría Pública", "Especialización en gerencia de proyectos"]


def _referencia():
    """Otra persona (referencia laboral): sus datos NO deben extraerse (etiqueta O)."""
    f = fake()
    n1, n2, a1, a2 = f.first_name().split()[0], f.first_name().split()[0], f.last_name(), f.last_name()
    lines = [f"{n1} {n2} {a1} {a2}", random.choice(CARGOS), f.company(), gen_phone(), gen_email(n1, a1, a2)]
    return [[(ln, None)] for ln in lines if random.random() < 0.9]


def doc_cv(p):
    """Hoja de vida: nombre como titulo sin etiqueta, contacto revuelto, otras personas como referencias."""
    f = fake()

    def rango():
        fin = random.choice(["Actualidad", f"{random.choice(MESES)} {random.randint(2020, 2025)}"])
        return f"({random.choice(MESES)} {random.randint(2012, 2023)} – {fin})"

    nombre = name_pieces(p, inverted=random.random() < 0.1, upper=random.random() < 0.7)
    cabecera = [nombre, [(random.choice(PROFESIONES), None)]]

    contacto = []
    if random.random() < 0.7:
        contacto.append([(f"Edad: {random.randint(18, 60)} años", None)])
    if random.random() < 0.5:
        contacto.append([(random.choice(["Sexo: femenino", "Sexo: masculino", "Estado civil: soltero"]), None)])
    contacto.append([(random.choice(["Teléfono", "Celular", "Móvil", "Tel."]) + ": ", None), (p["tel"], "TELEFONO")])
    if random.random() < 0.55:  # identificacion SIN tipo de documento
        contacto.append([("Identificación: ", None), (p["nd"], "NUM_DOC")])
    else:
        contacto.append([(random.choice(["Documento", "Identificación", "Doc. identidad"]) + ": ", None),
                         (p["td"], "TIPO_DOC"), (" ", None), (p["nd"], "NUM_DOC")])
    if random.random() < 0.9:
        contacto.append([("Correo: ", None), (p["em"], "EMAIL")])
    contacto.append([(f"Ciudad: {f.city()}", None)])
    if random.random() < 0.5:
        random.shuffle(contacto)
    bloque_contacto = join_lines(contacto, random.choice(["\n", "\n", " "]))

    pos = random.choice(["cabecera", "perfil", "perfil", "final"])
    perfil = [[("PERFIL PROFESIONAL", None)]]
    if pos == "perfil":
        perfil.append(bloque_contacto)
    perfil.append([(f.paragraph(nb_sentences=4), None)])

    exp = [[("EXPERIENCIA LABORAL", None)]]
    for _ in range(random.randint(1, 3)):
        exp.append([(f"{f.company()} – {random.choice(CARGOS)} {rango()}", None)])
        exp += [[(f.sentence(nb_words=8), None)] for _ in range(random.randint(1, 3))]

    edu = [[("EDUCACIÓN", None)]]
    for _ in range(random.randint(1, 3)):
        edu.append([(random.choice(TITULOS), None)])
        edu.append([(f"{random.choice(CENTROS)} ({random.randint(2005, 2025)})", None)])
    if random.random() < 0.6:  # colegio con apellidos: parece un nombre pero NO lo es
        edu.append([(f"Colegio {f.first_name().split()[0]} {f.last_name()} {f.last_name()} "
                     f"({random.randint(2005, 2022)})", None)])

    hab = [[("HABILIDADES TÉCNICAS", None)], [("Lenguajes: PHP, JavaScript, C#, Python", None)],
           [("Herramientas: Git, Azure DevOps", None)]]
    refs = [[("REFERENCIAS", None)]] + _referencia() + (_referencia() if random.random() < 0.7 else [])

    medio = [exp, edu, hab]
    random.shuffle(medio)
    lines = list(cabecera)
    if pos == "cabecera":
        lines.append(bloque_contacto)
    lines += perfil
    for sec in medio:
        lines += sec
    if random.random() < 0.8:
        lines += refs
    if pos == "final":
        lines.append(bloque_contacto)
    return join_lines(lines)


OCR_MAP = {"O": "0", "0": "O", "l": "1", "1": "l", "I": "1", "S": "5", "B": "8",
           "o": "0", "e": "c", "í": "i", "é": "e", "ó": "o", "á": "a"}


def ocr_noise(text, p=0.004):
    return "".join(OCR_MAP.get(c, c) if random.random() < p else c for c in text)


def build_example(pieces, upper=False, ocr=False):
    """Convierte piezas (texto, etiqueta) a (tokens, tags) BIO, usando el MISMO tokenizador de inferencia."""
    text = "".join(t for t, _ in pieces)
    spans, pos = [], 0
    for t, lab in pieces:
        if lab:
            spans.append((pos, pos + len(t), lab))
        pos += len(t)
    if upper:
        text = text.upper()
    if ocr:
        text = ocr_noise(text)
    toks = tokenize(text)
    tags, prev = [], None
    for _, s, _e in toks:
        idx = next((k for k, (a, b, _) in enumerate(spans) if a <= s < b), None)
        if idx is None:
            tags.append("O")
        else:
            tags.append(("I-" if idx == prev else "B-") + spans[idx][2])
        prev = idx
    return [t for t, _, _ in toks], tags


def noise_block(n):
    """n lineas o parrafos de relleno (sin entidades)."""
    f = fake()
    lines = []
    for _ in range(n):
        txt = f.paragraph(nb_sentences=random.randint(2, 6)) if random.random() < 0.3 else noise_line()
        lines.append([(txt, None)])
    return join_lines(lines, "\n")


def make_example():
    p = gen_person()
    style = random.choices(["form", "narr", "scatter", "cv"], [3, 2, 2, 4])[0]
    pieces = {"form": doc_form, "narr": doc_narrative, "scatter": doc_scatter, "cv": doc_cv}[style](p)
    if random.random() < 0.35:  # documento largo: los datos quedan enterrados entre mucha informacion
        antes = noise_block(random.randint(0, 120))
        despues = noise_block(random.randint(0, 120))
        if random.random() < 0.5:
            despues += [("\n\nREFERENCIAS\n", None)] + join_lines(_referencia() + _referencia(), "\n")
        pieces = (antes + [("\n\n", None)] if antes else []) + pieces + ([("\n\n", None)] + despues if despues else [])
    return build_example(pieces, upper=random.random() < 0.15, ocr=random.random() < 0.15)


def load_jsonl(path):
    data = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            assert len(d["tokens"]) == len(d["tags"]), "tokens y tags deben tener igual longitud"
            assert all(t in TAG2ID for t in d["tags"]), "etiqueta desconocida"
            data.append((d["tokens"], d["tags"]))
    return data


# ---------------------------------------------------------------------------
# 3. Modelo: embeddings (palabra + caracteres CNN + forma) -> BiLSTM -> CRF
# ---------------------------------------------------------------------------
MAX_CHARS = 40
UNK = 1


def encode_batch(batch_tokens, w2i, c2i, p_unk=0.0):
    import torch
    B, T = len(batch_tokens), max(len(t) for t in batch_tokens)
    words = torch.zeros(B, T, dtype=torch.long)
    chars = torch.zeros(B, T, MAX_CHARS, dtype=torch.long)
    shapes = torch.zeros(B, T, dtype=torch.long)
    mask = torch.zeros(B, T, dtype=torch.bool)
    for i, toks in enumerate(batch_tokens):
        for j, tok in enumerate(toks):
            wid = w2i.get(norm_word(tok), UNK)
            if p_unk and random.random() < p_unk:
                wid = UNK  # word dropout: obliga al modelo a apoyarse en caracteres y contexto
            words[i, j] = wid
            for k, ch in enumerate(tok[:MAX_CHARS]):
                chars[i, j, k] = c2i.get(ch, UNK)
            shapes[i, j] = SHAPE2ID[shape_of(tok)]
            mask[i, j] = True
    return words, chars, shapes, mask


def build_vocab(data, min_freq=2):
    wc = Counter(norm_word(t) for toks, _ in data for t in toks)
    w2i = {"<pad>": 0, "<unk>": 1}
    for w, c in wc.items():
        if c >= min_freq:
            w2i[w] = len(w2i)
    c2i = {"<pad>": 0, "<unk>": 1}
    for ch in sorted({ch for toks, _ in data for t in toks for ch in t}):
        c2i[ch] = len(c2i)
    return w2i, c2i


def make_model_class():
    import torch
    import torch.nn as nn
    from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence
    from torchcrf import CRF

    class BiLSTMCRF(nn.Module):
        def __init__(self, n_words, n_chars, n_shapes, n_tags, w_dim=100, c_dim=30,
                     c_filters=50, s_dim=8, hidden=128, layers=2, dropout=0.4):
            super().__init__()
            self.word_emb = nn.Embedding(n_words, w_dim, padding_idx=0)
            self.char_emb = nn.Embedding(n_chars, c_dim, padding_idx=0)
            self.char_cnn = nn.Conv1d(c_dim, c_filters, kernel_size=3, padding=1)
            self.shape_emb = nn.Embedding(n_shapes, s_dim, padding_idx=0)
            self.drop = nn.Dropout(dropout)
            self.lstm = nn.LSTM(w_dim + c_filters + s_dim, hidden, num_layers=layers,
                                bidirectional=True, batch_first=True, dropout=dropout)
            self.fc = nn.Linear(2 * hidden, n_tags)
            self.crf = CRF(n_tags, batch_first=True)

        def emissions(self, words, chars, shapes, mask):
            B, T, L = chars.shape
            c = self.char_emb(chars.view(B * T, L)).transpose(1, 2)
            c = torch.relu(self.char_cnn(c)).max(dim=2).values.view(B, T, -1)
            x = self.drop(torch.cat([self.word_emb(words), c, self.shape_emb(shapes)], dim=-1))
            lengths = mask.sum(1).cpu()
            packed = pack_padded_sequence(x, lengths, batch_first=True, enforce_sorted=False)
            out, _ = self.lstm(packed)
            out, _ = pad_packed_sequence(out, batch_first=True, total_length=T)
            return self.fc(self.drop(out))

        def loss(self, words, chars, shapes, mask, tags):
            return -self.crf(self.emissions(words, chars, shapes, mask), tags, mask=mask, reduction="mean")

        def decode(self, words, chars, shapes, mask):
            return self.crf.decode(self.emissions(words, chars, shapes, mask), mask=mask)

    return BiLSTMCRF


def predict_tags(model, batch_tokens, w2i, c2i, device):
    import torch
    model.eval()
    with torch.no_grad():
        words, chars, shapes, mask = [x.to(device) for x in encode_batch(batch_tokens, w2i, c2i)]
        pred = model.decode(words, chars, shapes, mask)
    return [[TAGS[i] for i in seq] for seq in pred]


def evaluate(model, data, w2i, c2i, device, bs=64):
    from seqeval.metrics import f1_score
    data = sorted(data, key=lambda x: len(x[0]))
    y_true, y_pred = [], []
    for i in range(0, len(data), bs):
        batch = data[i:i + bs]
        y_pred += predict_tags(model, [t for t, _ in batch], w2i, c2i, device)
        y_true += [g for _, g in batch]
    return f1_score(y_true, y_pred), y_true, y_pred


# ---------------------------------------------------------------------------
# 4. Entrenamiento
# ---------------------------------------------------------------------------
def ventanas(tokens, tags, window=256, stride=192, keep_empty=0.4):
    """Parte documentos largos en ventanas, como hace la inferencia. Conserva solo parte de las vacias."""
    if len(tokens) <= window:
        return [(tokens, tags)]
    out, start = [], 0
    while True:
        end = min(start + window, len(tokens))
        t, g = tokens[start:end], list(tags[start:end])
        if g and g[0].startswith("I-"):
            g[0] = "B-" + g[0][2:]
        if any(x != "O" for x in g) or random.random() < keep_empty:
            out.append((t, g))
        if end == len(tokens):
            break
        start += stride
    return out or [(tokens[:window], list(tags[:window]))]


def cmd_train(a):
    import torch
    from seqeval.metrics import classification_report
    random.seed(a.seed)
    torch.manual_seed(a.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Dispositivo: {device}")

    print(f"Generando {a.synth} documentos sinteticos...")
    synth = [make_example() for _ in range(a.synth)]
    synth = [e for e in synth if len(e[0]) > 0]
    n_dev = max(200, int(0.05 * len(synth)))
    dev_synth, train = synth[:n_dev], synth[n_dev:]

    dev_real = []
    if a.real:
        real = load_jsonl(a.real)
        random.shuffle(real)
        k = max(1, int(0.25 * len(real)))
        dev_real = real[:k]
        train += real[k:] * a.real_weight
        print(f"Datos reales: {len(real)} (dev={len(dev_real)}, train x{a.real_weight})")
    train = [w for t, g in train for w in ventanas(t, g)]
    dev_synth = [w for t, g in dev_synth for w in ventanas(t, g, keep_empty=1.0)]
    dev_real = [w for t, g in dev_real for w in ventanas(t, g, keep_empty=1.0)]
    print(f"Ventanas de entrenamiento: {len(train)} | dev sintetico: {len(dev_synth)}")
    random.shuffle(train)
    sel_name, sel_set = ("real", dev_real) if dev_real else ("sintetico", dev_synth)

    w2i, c2i = build_vocab(train)
    cfg = dict(n_words=len(w2i), n_chars=len(c2i), n_shapes=len(SHAPES), n_tags=len(TAGS))
    Model = make_model_class()
    model = Model(**cfg).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-4)
    print(f"Parametros: {sum(p.numel() for p in model.parameters()):,} | train={len(train)}")

    best = -1.0
    for epoch in range(1, a.epochs + 1):
        model.train()
        random.shuffle(train)
        total = 0.0
        for i in range(0, len(train), a.batch):
            batch = train[i:i + a.batch]
            words, chars, shapes, mask = [x.to(device) for x in
                                          encode_batch([t for t, _ in batch], w2i, c2i, p_unk=0.1)]
            T = words.shape[1]
            tags = torch.zeros(len(batch), T, dtype=torch.long)
            for r, (_, g) in enumerate(batch):
                tags[r, :len(g)] = torch.tensor([TAG2ID[x] for x in g])
            tags = tags.to(device)
            opt.zero_grad()
            loss = model.loss(words, chars, shapes, mask, tags)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            opt.step()
            total += loss.item() * len(batch)
        f1_s, _, _ = evaluate(model, dev_synth, w2i, c2i, device)
        f1_sel = f1_s
        msg = f"Epoca {epoch:02d} | loss={total / len(train):.4f} | F1 sintetico={f1_s:.4f}"
        if dev_real:
            f1_sel, _, _ = evaluate(model, dev_real, w2i, c2i, device)
            msg += f" | F1 real={f1_sel:.4f}"
        print(msg)
        if f1_sel > best:
            best = f1_sel
            torch.save({"state_dict": model.state_dict(), "w2i": w2i, "c2i": c2i, "config": cfg}, a.out)
            print(f"  -> guardado en {a.out}")

    ck = torch.load(a.out, map_location=device, weights_only=False)
    model.load_state_dict(ck["state_dict"])
    _, yt, yp = evaluate(model, sel_set, w2i, c2i, device)
    print(f"\nReporte final ({sel_name}):")
    print(classification_report(yt, yp, digits=3))


def cmd_gen(a):
    random.seed(a.seed)
    with open(a.out, "w", encoding="utf-8") as f:
        for _ in range(a.n):
            toks, tags = make_example()
            f.write(json.dumps({"tokens": toks, "tags": tags}, ensure_ascii=False) + "\n")
    print(f"{a.n} ejemplos en {a.out}")


# ---------------------------------------------------------------------------
# 5. Lectura interna de archivos (adaptador invisible para el usuario)
# ---------------------------------------------------------------------------
IMG_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
_reader = None

_ETIQUETAS = (r"Edad|Sexo|Identificaci[oó]n|Ciudad|Tel[eé]fono|Celular|M[oó]vil|Correo(?: electr[oó]nico)?|"
              r"E-?mail|Direcci[oó]n|Documento|C[eé]dula|Nombres?|Apellidos?|Estado civil|Nacionalidad|Contacto")
_ESTRUCTURA = (r"Colegio|Universidad|Fundaci[oó]n|Corporaci[oó]n|Instituci[oó]n|Bachiller|T[eé]cnico|"
               r"Tecn[oó]log[oa]|Profesional|Referencias|Experiencia|Educaci[oó]n|Formaci[oó]n|Habilidades|Perfil")
_ENCABEZADOS = (r"REFERENCIAS PERSONALES|REFERENCIAS LABORALES|REFERENCIAS FAMILIARES|REFERENCIAS|"
                r"EXPERIENCIA LABORAL|EDUCACI[OÓ]N|FORMACI[OÓ]N ACAD[EÉ]MICA|HABILIDADES T[EÉ]CNICAS|HABILIDADES|"
                r"PERFIL PROFESIONAL|DATOS PERSONALES|IDIOMAS|CURSOS|LOGROS|OBJETIVO PROFESIONAL")


def _separar_email(m):
    e = m.group(0)
    c = EMAIL_TLD_RE.match(e)
    resto = e[len(c.group(0)):] if c else ""
    if c and resto and "." not in resto:  # "gmail.comEXPERIENCIA" -> "gmail.com EXPERIENCIA"
        return c.group(0) + " " + resto
    return e


_ICONO_RE = re.compile(
    r"^\s*[^\w\s]{0,3}\w{0,2}[^\w\s]{0,3}\s+((?:\+?57[\s-]?)?3\d{2}[\s.-]?\d{3}[\s.-]?\d{4}"
    r"|[\w.+-]+@[\w-]+(?:\.[\w-]+)+)\s*$")


def _limpiar_iconos(t):
    """Quita la basura que el OCR lee de los iconos ("@ 3239739884", "(7) correo@x.com")."""
    out = []
    for linea in t.split("\n"):
        m = _ICONO_RE.match(linea)
        out.append(m.group(1) if m else linea)
    return "\n".join(out)


def normalizar_texto(text):
    """Repara texto con lineas pegadas (muy comun al extraer PDF): separa etiquetas, secciones y emails."""
    if not text:
        return ""
    t = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    t = _limpiar_iconos(t)
    t = re.sub(r"(?<=\S)(?=(?:" + _ETIQUETAS + r")\s*:)", "\n", t)             # ...femeninoIdentificación:
    t = re.sub(r"(?<=[a-záéíóúñ0-9.)])(?=(?:" + _ESTRUCTURA + r")\b)", "\n", t)  # ...softwareColegio
    t = re.sub(r"(?<=[a-záéíóúñ.)])(?=[A-ZÁÉÍÓÚÑ]{4,}(?![a-záéíóúñ]))", "\n", t)   # ...SoftwarePERFIL
    t = re.sub("(" + _ENCABEZADOS + ")", r"\n\1\n", t)                          # EDUCACIÓNHABILIDADES...
    t = re.sub(r"(\d{7,})(?=[A-Za-z][\w.+-]*@)", r"\1 ", t)                     # 3108880321cine@...
    t = EMAIL_RE.sub(_separar_email, t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _lineas(cajas, mh):
    """Agrupa cajas (x0, y0, x1, y1, texto) en lineas segun su altura; devuelve [{y0, y1, texto}]."""
    grupos = []
    for c in sorted(cajas, key=lambda c: (c[1] + c[3]) / 2):
        cy = (c[1] + c[3]) / 2
        if grupos and abs(cy - grupos[-1]["cy"]) <= 0.6 * mh:
            grupos[-1]["cajas"].append(c)
        else:
            grupos.append({"cy": cy, "cajas": [c]})
    out = []
    for g in grupos:
        cs = sorted(g["cajas"], key=lambda c: c[0])
        out.append({"y0": min(c[1] for c in cs), "y1": max(c[3] for c in cs),
                    "texto": " ".join(c[4] for c in cs)})
    return out


def _columnas(cajas, ancho):
    """Divide en columnas si hay un hueco vertical vacio claro entre el 20 % y el 80 % del ancho."""
    nb = 200
    cubierto = [False] * (nb + 1)
    for c in cajas:
        for b in range(int(c[0] / ancho * nb), min(nb, int(c[2] / ancho * nb)) + 1):
            cubierto[b] = True
    mejor, b = (0, 0, 0), int(0.2 * nb)
    while b < int(0.8 * nb):
        if cubierto[b]:
            b += 1
            continue
        j = b
        while j < nb and not cubierto[j]:
            j += 1
        if j - b > mejor[0]:
            mejor = (j - b, b, j)
        b = j
    if mejor[0] < 0.06 * nb:
        return [cajas]
    corte = (mejor[1] + mejor[2]) / 2 / nb * ancho
    izq = [c for c in cajas if (c[0] + c[2]) / 2 < corte]
    der = [c for c in cajas if (c[0] + c[2]) / 2 >= corte]
    return [col for col in (izq, der) if col]


def _cajas_a_texto(cajas, ancho, alto):
    """Convierte las cajas del OCR en texto ordenado: titulo (letra muy grande) primero, luego columnas."""
    cajas = [c for c in cajas if c[4].strip()]
    if not cajas:
        return ""
    alturas = sorted(c[3] - c[1] for c in cajas)
    mh = alturas[len(alturas) // 2] or 1
    grandes = [c for c in cajas if (c[3] - c[1]) >= 1.7 * mh and c[1] < 0.35 * alto]
    partes = []
    if grandes and sum(len(c[4].split()) for c in grandes) <= 8:  # el nombre suele ser el texto mas grande
        partes.append(" ".join(l["texto"] for l in _lineas(grandes, mh)))
        ids = {id(c) for c in grandes}
        cajas = [c for c in cajas if id(c) not in ids]
    for col in _columnas(cajas, ancho) if cajas else []:
        bloque, prev = [], None
        for ln in _lineas(col, mh):
            if prev is not None and ln["y0"] - prev["y1"] > 1.2 * mh:  # hueco grande = nueva seccion
                bloque.append("")
            bloque.append(ln["texto"])
            prev = ln
        partes.append("\n".join(bloque))
    return "\n\n".join(partes)


def _ocr_reader():
    global _reader
    if _reader is None:
        import easyocr
        import torch
        _reader = easyocr.Reader(["es", "en"], gpu=torch.cuda.is_available())
    return _reader


def _cargar_imagen(path):
    import numpy as np
    from PIL import Image, ImageOps
    return np.array(ImageOps.exif_transpose(Image.open(path)).convert("RGB"))


def _ocr(arr):
    """OCR con posiciones (array RGB): reconstruye columnas y pone el titulo grande primero."""
    alto, ancho = arr.shape[:2]
    cajas = []
    for pts, txt, _conf in _ocr_reader().readtext(arr, detail=1, paragraph=False):
        if txt.strip():
            xs, ys = [q[0] for q in pts], [q[1] for q in pts]
            cajas.append((min(xs), min(ys), max(xs), max(ys), txt.strip()))
    return _cajas_a_texto(cajas, ancho, alto)


def _pdf_texto(path):
    """Texto del PDF conservando los saltos de linea (MarkItDown los pega)."""
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        return "\n\n".join((pg.extract_text() or "") for pg in pdf.pages)


def _pdf_ocr(path):
    """PDF escaneado: cada pagina se convierte a imagen y se lee con OCR."""
    import numpy as np
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        return "\n\n".join(_ocr(np.array(pg.to_image(resolution=200).original.convert("RGB")))
                           for pg in pdf.pages)


def _markitdown(src):
    from markitdown import MarkItDown
    return MarkItDown().convert(src).text_content or ""


def read_file(path):
    s = str(path)
    if s.startswith(("http://", "https://")):
        return normalizar_texto(_markitdown(s))
    p = Path(s)
    ext = p.suffix.lower()
    text = ""
    if ext in (".md", ".markdown", ".txt"):
        text = p.read_text(encoding="utf-8", errors="ignore")
    elif ext in IMG_EXT:
        text = _ocr(_cargar_imagen(s))
    elif ext == ".pdf":
        for lector in (_pdf_texto, _markitdown):
            try:
                text = lector(s)
            except Exception as e:
                print(f"Aviso: {lector.__name__} fallo ({e})", file=sys.stderr)
                text = ""
            if len(text.strip()) >= 30:
                break
        if len(text.strip()) < 30:  # PDF escaneado
            text = _pdf_ocr(s)
    else:
        try:
            text = _markitdown(s)
        except Exception as e:
            print(f"Aviso: markitdown fallo ({e})", file=sys.stderr)
    return normalizar_texto(text)


# ---------------------------------------------------------------------------
# 6. Inferencia + validacion con regex
# ---------------------------------------------------------------------------
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"(?:\+?57[\s-]?)?3\d{2}[\s.-]?\d{3}[\s.-]?\d{4}")
# Email que termina en un dominio conocido (corta texto pegado, p. ej. "gmail.comEXPERIENCIA")
EMAIL_TLD_RE = re.compile(
    r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*?\.(?:com|net|org|edu|gov|info|biz|co|es|mx|ar|cl|pe|ec|io|me)"
    r"(?:\.(?:co|mx|ar|es|uk|pe|cl|ec))?", re.I)
TIPO_DOC_RE = re.compile(
    r"^(?:c\.?\s?c\.?|t\.?\s?i\.?|c\.?\s?e\.?|p\.?\s?a\.?\s?s?\.?|pasaporte|nit|dni|rc"
    r"|c[eé]dula(?:\s+de\s+(?:ciudadan[ií]a|extranjer[ií]a))?|tarjeta\s+de\s+identidad|registro\s+civil)$", re.I)
DOC_FALLBACK_RE = re.compile(
    r"\b(?:identificaci[oó]n|documento(?:\s+de\s+identidad)?|c[eé]dula(?:\s+de\s+ciudadan[ií]a)?|c\.?\s?c\.?)"
    r"\s*(?:n[oº°]\.?|#|:)*\s*:?\s*([0-9][0-9.]{5,13})", re.I)


def clean_email(s):
    if not s:
        return None
    s = s.strip().rstrip(".,;:)")
    m = EMAIL_TLD_RE.match(s)
    if m:
        return m.group(0).lower()
    return s.lower() if EMAIL_RE.fullmatch(s) else None


def load_model(path, device):
    import torch
    ck = torch.load(path, map_location=device, weights_only=False)
    model = make_model_class()(**ck["config"]).to(device)
    model.load_state_dict(ck["state_dict"])
    model.eval()
    return model, ck["w2i"], ck["c2i"]


def spans_from_tags(tags):
    spans, cur = [], None
    for i, t in enumerate(tags):
        if t == "O":
            cur = None
            continue
        pre, lab = t.split("-", 1)
        if pre == "B" or cur is None or cur[0] != lab:
            cur = [lab, i, i]
            spans.append(cur)
        else:
            cur[2] = i
    return spans


def tag_document(text, model, w2i, c2i, device, window=256, stride=192):
    toks = tokenize(text)
    tokens = [t for t, _, _ in toks]
    tags = [None] * len(tokens)
    overlap = window - stride
    start = 0
    while start < len(tokens):
        end = min(start + window, len(tokens))
        pred = predict_tags(model, [tokens[start:end]], w2i, c2i, device)[0]
        for k, t in enumerate(pred):
            i = start + k
            if tags[i] is None or k >= overlap // 2:
                tags[i] = t
        if end == len(tokens):
            break
        start += stride
    return toks, [t or "O" for t in tags]


def clean_name(s):
    if s.isupper():
        s = " ".join(w.lower() if w.lower() in PARTICLE_SET else w.capitalize() for w in s.split())
    return s.strip()


NOMBRE_LABELS = ("PRIMER_NOMBRE", "SEGUNDO_NOMBRE", "PRIMER_APELLIDO", "SEGUNDO_APELLIDO")
# Palabras que, justo antes de un "nombre", indican que es una institucion y no una persona
INSTITUCION = {"colegio", "universidad", "fundacion", "corporacion", "institucion", "educativa", "escuela",
               "liceo", "gimnasio", "hospital", "clinica", "empresa", "compania", "sas", "ltda", "sena",
               "instituto", "politecnico", "fondo", "banco", "cooperativa", "centro", "jardin", "facultad",
               "academia", "iglesia", "parroquia", "grupo"}
# Palabras que NO pueden ser un apellido (evita extender "Correa" con "Estudiante", "Perfil", etc.)
STOP_APELLIDO = {"de", "del", "la", "los", "las", "y", "e", "en", "con", "para", "por", "el", "estudiante",
                 "ingeniero", "ingeniera", "tecnologo", "tecnologa", "tecnico", "tecnica", "analista", "auxiliar",
                 "contador", "contadora", "abogado", "abogada", "desarrollador", "desarrolladora", "licenciado",
                 "licenciada", "profesional", "administrador", "administradora", "disenador", "disenadora",
                 "docente", "bachiller", "perfil", "experiencia", "educacion", "formacion", "datos", "hoja",
                 "curriculum", "vida", "contacto", "telefono", "correo", "email", "celular", "direccion",
                 "ciudad", "edad", "sexo", "identificacion", "documento", "cedula", "referencias"}


def _norm(tok):
    return strip_accents(tok.lower())


_OCR_DIGITOS = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "|": "1", "S": "5", "B": "8", "Z": "2"})


def _corregir_digitos(x):
    """En cadenas casi todo digitos, corrige letras que el OCR confunde con numeros."""
    x = x or ""
    solo = re.sub(r"[\s.\-]", "", x)
    if len(solo) >= 8 and re.fullmatch(r"[\dOolI|SBZ]+", solo) and sum(c.isdigit() for c in solo) >= 0.8 * len(solo):
        return x.translate(_OCR_DIGITOS)
    return x


def norm_phone(x):
    d = re.sub(r"\D", "", _corregir_digitos(x))
    d = d[2:] if len(d) == 12 and d.startswith("57") else d
    return d if re.fullmatch(r"3\d{9}", d) else None


def valida_num_doc(x):
    nd = re.sub(r"[.\s-]", "", _corregir_digitos(x))
    return nd if re.fullmatch(r"[A-Za-z0-9]{5,15}", nd) else None


def _clusters(spans):
    """Agrupa spans de nombre/apellido que estan pegados: cada grupo es una persona candidata."""
    clusters, cur = [], None
    for sp in spans:
        if sp[0] not in NOMBRE_LABELS:
            continue
        if cur and sp[1] == cur[-1][2] + 1:
            cur.append(sp)
        else:
            cur = [sp]
            clusters.append(cur)
    return clusters


def _extender_apellido(cl, tokens, tags):
    """Red de seguridad: si falta el 2.o apellido y la palabra siguiente tiene el mismo formato, se agrega."""
    last = cl[-1]
    if last[0] != "PRIMER_APELLIDO" or any(sp[0] == "SEGUNDO_APELLIDO" for sp in cl):
        return
    i = last[2] + 1
    if i >= len(tokens) or tags[i] != "O":
        return
    t, prev = tokens[i], tokens[last[2]]
    mismo_formato = (prev.isupper() and t.isupper()) or (prev.istitle() and t.istitle())
    if t.isalpha() and len(t) >= 3 and mismo_formato and _norm(t) not in STOP_APELLIDO:
        cl.append(["SEGUNDO_APELLIDO", i, i])


def _puntaje(cl, tokens, refs_idx):
    labs = {sp[0] for sp in cl}
    score = 10 * len(labs)
    i, k = cl[0][1] - 1, 0
    while i >= 0 and k < 3 and tokens[i] != "\n":  # ultimas palabras antes, en la misma linea
        if _norm(tokens[i]) in INSTITUCION:
            score -= 50
            break
        i, k = i - 1, k + 1
    if refs_idx is not None and cl[0][1] > refs_idx:  # despues del titulo "REFERENCIAS"
        score -= 100
    if not (labs & {"PRIMER_NOMBRE", "SEGUNDO_NOMBRE"}) or not (labs & {"PRIMER_APELLIDO", "SEGUNDO_APELLIDO"}):
        score -= 15
    return score


NO_ES_NOMBRE = {"hoja", "vida", "curriculum", "curriculo", "cv", "perfil", "datos", "personales", "contrato",
                "certificado", "certificacion", "constancia", "solicitud", "carta", "informe", "factura", "acta",
                "formulario", "registro", "autorizacion", "poder", "resumen", "informacion", "contacto",
                "documento", "presentacion", "arrendamiento", "laboral", "profesional", "referencias"}


def _cap(w):
    return w.lower() if w.lower() in PARTICLE_SET else (w.capitalize() if w.isupper() else w[:1].upper() + w[1:])


def nombre_por_encabezado(text):
    """Respaldo: la primera linea es el nombre si parece uno (MAYUSCULAS o con mayuscula inicial)."""
    prohibidas = NO_ES_NOMBRE | INSTITUCION | (STOP_APELLIDO - PARTICLE_SET)
    for k, linea in enumerate([l.strip() for l in text.splitlines() if l.strip()][:3]):
        pal = linea.split()
        if not (2 <= len(pal) <= 6 and all(re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ'-]+", w) for w in pal)):
            continue
        if any(_norm(w) in prohibidas for w in pal):
            continue
        propias = [w for w in pal if w.lower() not in PARTICLE_SET]
        mayus = sum(w[0].isupper() for w in propias)
        if not (linea.isupper() or (k == 0 and mayus >= 2 and mayus >= 0.6 * len(propias))):
            continue
        unidades, i = [], 0
        while i < len(pal):  # une particulas con la palabra siguiente: "DE LA CRUZ"
            if pal[i].lower() in PARTICLE_SET and i + 1 < len(pal):
                j = i
                while j + 1 < len(pal) and pal[j].lower() in PARTICLE_SET:
                    j += 1
                unidades.append(" ".join(_cap(w) for w in pal[i:j + 1]))
                i = j + 1
            else:
                unidades.append(_cap(pal[i]))
                i += 1
        orden = {2: ("PRIMER_NOMBRE", "PRIMER_APELLIDO"),
                 3: ("PRIMER_NOMBRE", "PRIMER_APELLIDO", "SEGUNDO_APELLIDO"),
                 4: ("PRIMER_NOMBRE", "SEGUNDO_NOMBRE", "PRIMER_APELLIDO", "SEGUNDO_APELLIDO")}.get(len(unidades))
        if orden:
            return dict(zip(orden, unidades))
    return None


def extract(text, model, w2i, c2i, device):
    toks, tags = tag_document(text, model, w2i, c2i, device)
    tokens = [t for t, _, _ in toks]
    spans = spans_from_tags(tags)

    def txt(sp):
        return text[toks[sp[1]][1]:toks[sp[2]][2]].strip()

    # Donde empieza la seccion de referencias (datos de OTRAS personas)
    refs_idx = next((i for i, t in enumerate(tokens)
                     if _norm(t) in ("referencia", "referencias") and (i == 0 or tokens[i - 1] == "\n")
                     and (i + 1 >= len(tokens) or tokens[i + 1] != ":")), None)

    # 1) Elegir UNA persona: el grupo de nombre/apellido con mejor puntaje (empate: el primero del documento)
    clusters = _clusters(spans)
    for cl in clusters:
        _extender_apellido(cl, tokens, tags)
    scored = [(_puntaje(cl, tokens, refs_idx), cl) for cl in clusters]
    res = {lab: None for lab in ENTITIES}
    ancla, fuente = 0, None
    if scored:
        puntaje, mejor = max(scored, key=lambda x: (x[0], -x[1][0][1]))
        if puntaje > 0:  # un grupo con puntaje <= 0 (colegio, referencias, incompleto) no se acepta
            ancla, fuente = mejor[0][1], "modelo"
            for lab in NOMBRE_LABELS:
                sp = next((q for q in mejor if q[0] == lab), None)
                if sp:
                    res[lab] = clean_name(txt(sp))

    # 2) Datos de contacto: el valido mas cercano a esa persona. Lo que esta bajo "REFERENCIAS" es de otras personas.
    campos = {"TIPO_DOC": lambda x: x if TIPO_DOC_RE.match(x) else None,
              "EMAIL": clean_email, "TELEFONO": norm_phone, "NUM_DOC": valida_num_doc}

    def elegir(label, validar):
        cands = sorted((q for q in spans if q[0] == label and (refs_idx is None or q[1] < refs_idx)),
                       key=lambda q: abs(q[1] - ancla))
        for q in cands:
            v = validar(txt(q))
            if v:
                return v
        return None

    for lab, validar in campos.items():
        res[lab] = elegir(lab, validar)
    if res["NUM_DOC"] and res["NUM_DOC"] == res["TELEFONO"]:
        res["NUM_DOC"] = None

    # 3) Respaldo con regex si el modelo no encontro el dato (solo antes de "REFERENCIAS")
    util = text[:toks[refs_idx][1]] if refs_idx is not None else text
    if not res["EMAIL"]:
        m = EMAIL_RE.search(util)
        res["EMAIL"] = clean_email(m.group()) if m else None
    if not res["TELEFONO"]:
        m = PHONE_RE.search(util)
        res["TELEFONO"] = norm_phone(m.group()) if m else None
    if not res["NUM_DOC"]:
        m = DOC_FALLBACK_RE.search(util)
        if m:
            nd = m.group(1).replace(".", "")
            if re.fullmatch(r"\d{5,12}", nd) and nd != res["TELEFONO"]:
                res["NUM_DOC"] = nd

    # 4) Sin nombre del modelo: usar el titulo, pero solo si el documento trae datos de contacto (es de una persona)
    if fuente is None and any(res[k] for k in ("EMAIL", "TELEFONO", "NUM_DOC")):
        por_titulo = nombre_por_encabezado(text)
        if por_titulo:
            res.update(por_titulo)
            fuente = "encabezado"

    out = {lab.lower(): v for lab, v in res.items()}
    out["_personas_detectadas"] = sum(1 for sc, _ in scored if sc > 0)
    out["_fuente_nombre"] = fuente  # "modelo", "encabezado" (respaldo) o None
    return out


def cmd_leer(a):
    """Muestra el texto que el modelo recibe de un archivo (util para revisar la lectura/OCR)."""
    print(read_file(a.file))


def cmd_diagnostico(a):
    """Muestra paso a paso que lee y que marca el modelo, para saber donde falla."""
    import torch
    print(f"Version del codigo: {VERSION}\nArchivo en uso:    {Path(__file__).resolve()}")
    text = read_file(a.file)
    print(f"\n--- 1) TEXTO LEIDO ({len(text)} caracteres; primeros 700) ---\n{text[:700]}")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model, w2i, c2i = load_model(a.model, device)
    toks, tags = tag_document(text, model, w2i, c2i, device)
    spans = spans_from_tags(tags)
    print("\n--- 2) ENTIDADES QUE MARCA EL MODELO (en bruto) ---")
    for lab, st, en in spans:
        print(f"  {lab:18} {text[toks[st][1]:toks[en][2]]!r}")
    if not spans:
        print("  (ninguna)")
    print("\n--- 3) RESULTADO FINAL ---")
    print(json.dumps(extract(text, model, w2i, c2i, device), ensure_ascii=False, indent=2))


def cmd_predict(a):
    import torch
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model, w2i, c2i = load_model(a.model, device)
    text = read_file(a.file)
    if not text.strip():
        sys.exit("No se pudo leer texto del archivo.")
    print(json.dumps(extract(text, model, w2i, c2i, device), ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gen", help="genera datos sinteticos en JSONL")
    g.add_argument("--n", type=int, default=20)
    g.add_argument("--out", default="muestra.jsonl")
    g.add_argument("--seed", type=int, default=1)
    g.set_defaults(fn=cmd_gen)

    t = sub.add_parser("train", help="entrena el modelo")
    t.add_argument("--synth", type=int, default=8000, help="n de documentos sinteticos")
    t.add_argument("--real", default=None, help="JSONL con datos reales anotados")
    t.add_argument("--real_weight", type=int, default=3, help="veces que se repiten los datos reales")
    t.add_argument("--epochs", type=int, default=12)
    t.add_argument("--batch", type=int, default=32)
    t.add_argument("--lr", type=float, default=2e-3)
    t.add_argument("--seed", type=int, default=42)
    t.add_argument("--out", default="modelo.pt")
    t.set_defaults(fn=cmd_train)

    r = sub.add_parser("leer", help="muestra el texto leido de un archivo (sin modelo)")
    r.add_argument("file")
    r.set_defaults(fn=cmd_leer)

    d = sub.add_parser("diagnostico", help="muestra el texto leido, lo que marca el modelo y el resultado")
    d.add_argument("file")
    d.add_argument("--model", default="modelo.pt")
    d.set_defaults(fn=cmd_diagnostico)

    p = sub.add_parser("predict", help="extrae los campos de un archivo")
    p.add_argument("file")
    p.add_argument("--model", default="modelo.pt")
    p.set_defaults(fn=cmd_predict)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()