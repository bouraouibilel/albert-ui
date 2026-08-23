import os
import sys
import argparse
import asyncio
import fnmatch
from pathlib import Path

# Ajouter le répertoire backend au sys.path pour les imports app
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.converter import DocumentConverter
from app.services.albert_client import albert_client
from app.core.config import settings
from app.core.logger import log_event

SUPPORTED_EXTENSIONS = {'.pdf', '.docx', '.doc', '.xlsx', '.xls', '.html', '.htm', '.txt', '.md'}

async def process_file(file_path: Path, collection_name: str, output_dir: Path, ingest: bool = False):
    filename = file_path.name
    print(f"\n📄 Traitement de : '{filename}'...")
    log_event("BATCH", f"🚀 Traitement du fichier '{filename}'")

    try:
        # Conversion Markdown avec extraction d'images hiérarchisée et transcription UML
        result = await DocumentConverter.convert_to_markdown(
            file_path=str(file_path),
            filename=filename,
            collection_name=collection_name
        )

        md_content = result["markdown_content"]
        md_filename = f"{file_path.stem}.md"
        output_file = output_dir / md_filename

        output_dir.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(md_content)

        print(f"   ✅ Converti avec succès -> {output_file} ({result['char_count']} caractères, {result.get('pages_count', 1)} pages)")

        if ingest:
            if not collection_name:
                print("   ⚠️ Option --ingest spécifiée mais aucune collection cible fournie (--collection). Indexation ignorée.")
            else:
                print(f"   📤 Indexation dans la collection Albert '{collection_name}'...")
                ingest_res = await albert_client.create_document_from_text(
                    collection_id=collection_name,
                    content=md_content,
                    filename=md_filename
                )
                doc_id = ingest_res.get("id") or ingest_res.get("document_id") or "OK"
                print(f"   ✨ Indexé avec succès dans Albert API (Doc ID: {doc_id})")
                log_event("BATCH", f"✨ Document '{filename}' indexé dans collection '{collection_name}'")

        return True
    except Exception as e:
        print(f"   ❌ Erreur lors du traitement de '{filename}': {e}")
        log_event("BATCH", f"❌ Erreur sur '{filename}': {e}", level="ERROR")
        return False

async def main():
    parser = argparse.ArgumentParser(description="Albert Studio - Script de conversion par lot avec filtre de nom de fichier")
    parser.add_argument("--dir", "-d", default=os.path.join("storage", "uploads"), help="Répertoire contenant les fichiers source (Défaut: storage/uploads)")
    parser.add_argument("--filter", "-f", default="*", help="Filtre sur le nom de fichier (ex: '*PASRAU*.docx', 'SFD*', '*.pdf')")
    parser.add_argument("--collection", "-c", default="default", help="Nom ou ID de la collection Albert cible")
    parser.add_argument("--output", "-o", default=os.path.join("storage", "markdown"), help="Répertoire de sortie pour les fichiers .md générés")
    parser.add_argument("--recursive", "-r", action="store_true", help="Parcourir les sous-dossiers récursivement")
    parser.add_argument("--ingest", "-i", action="store_true", help="Indexer automatiquement les fichiers convertis dans Albert API")

    args = parser.parse_args()

    source_dir = Path(args.dir)
    output_dir = Path(args.output)
    file_filter = args.filter

    if not source_dir.exists() or not source_dir.is_dir():
        print(f"❌ Le répertoire source '{source_dir}' n'existe pas.")
        sys.exit(1)

    print("=" * 70)
    print("🚀 ALBERT RAG - CONVERSION PAR LOT DE DOCUMENTS")
    print(f"📁 Répertoire source : {source_dir.resolve()}")
    print(f"🔍 Filtre appliqué   : '{file_filter}'")
    print(f"🗂️  Collection cible  : '{args.collection}'")
    print(f"💾 Dossier de sortie : {output_dir.resolve()}")
    print(f"🔁 Récursif         : {'Oui' if args.recursive else 'Non'}")
    print(f"📤 Auto-indexation  : {'Oui' if args.ingest else 'Non'}")
    print("=" * 70)

    # Recherche des fichiers
    matched_files = []
    pattern = source_dir.rglob("*") if args.recursive else source_dir.glob("*")

    for file_path in pattern:
        if file_path.is_file() and file_path.suffix.lower() in SUPPORTED_EXTENSIONS:
            # Vérifier si le nom de fichier correspond au filtre (glob ou sous-chaîne insensible à la casse)
            name = file_path.name
            if fnmatch.fnmatch(name.lower(), file_filter.lower()) or (file_filter.lower() in name.lower()):
                matched_files.append(file_path)

    if not matched_files:
        print(f"⚠️ Aucun fichier supporté trouvé correspondant au filtre '{file_filter}' dans '{source_dir}'.")
        sys.exit(0)

    print(f"🎯 {len(matched_files)} fichier(s) trouvé(s) à traiter :\n" + "\n".join([f"   - {f.name}" for f in matched_files]))

    success_count = 0
    fail_count = 0

    for file_path in matched_files:
        ok = await process_file(file_path, args.collection, output_dir, args.ingest)
        if ok:
            success_count += 1
        else:
            fail_count += 1

    print("\n" + "=" * 70)
    print(f"🏁 TERMINÉ | Succès : {success_count}/{len(matched_files)} | Échecs : {fail_count}")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(main())
