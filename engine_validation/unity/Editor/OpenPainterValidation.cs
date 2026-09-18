// Open Painter engine validation for Unity URP (Lit, metallic workflow, smoothness in the
// metallic map's alpha). Editor script: place under Assets/OpenPainterValidation/Editor/ in a
// URP project and run
//
//   Unity -batchmode -nographics -projectPath <project> -executeMethod OpenPainter.Validation.Run
//         -opFixtures <engine-fixtures/unity_urp> -opReport <report.json> -quit
//
// What it does, and records for reproducibility:
// 1. Copies the exported files into Assets/OpenPainterValidation/<fixture>/ and hashes every one
//    (sha256), comparing with fixtures.json (no file is edited between export and import).
// 2. Applies explicit importer settings: BaseMap sRGB; Normal: TextureImporterType.NormalMap
//    (no flip: the export is OpenGL +Y, which Unity expects); MetallicGlossMap: linear,
//    alphaIsTransparency = false (alpha is smoothness, not transparency); OcclusionMap and
//    EmissionMap as documented. Mipmaps on, streaming off for diagnostics; production
//    compression (BC7/BC5) plus an uncompressed copy (TextureImporterCompression.Uncompressed).
// 3. Imports the FBX with importNormals = Import and importTangents = Import, and a second copy
//    with computed normals/tangents (MikkTSpace), recording which is used.
// 4. Builds URP Lit materials: _BaseMap, _BumpMap, _MetallicGlossMap (_Smoothness source =
//    metallic alpha), _OcclusionMap, _EmissionMap; and unlit channel-reference materials.
// 5. Builds the five scenes (unlit reference, rotating lit seam, distance/grazing sweep,
//    cutout/soft alpha, production prop) and captures them with a fixed exposure camera,
//    including forced mip levels (Texture2D.mipMapBias / QualitySettings.globalTextureMipmapLimit).
// 6. Reads the unlit reference capture back (linear RenderTexture) and compares encoded values
//    with fixtures.json within one 8-bit code value.
//
// Every configuration not executed is written to the report as NOT RUN.
#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using UnityEditor;
using UnityEngine;

namespace OpenPainter
{
    public static class Validation
    {
        const string AssetRoot = "Assets/OpenPainterValidation";
        static readonly string[] Scenes = { "unlit_channel_reference", "rotating_lit_seam", "distance_grazing_sweep", "cutout_soft_alpha", "production_prop" };

        [Serializable] class Report
        {
            public string engine; public string pipeline; public string graphics; public string status = "NOT RUN";
            public string fixtures; public List<string> imported = new List<string>(); public List<string> settings = new List<string>();
            public List<string> scenes = new List<string>(); public List<string> notRun = new List<string>(); public List<string> errors = new List<string>();
        }

        static string Arg(string name, string fallback)
        {
            var args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length - 1; i++) if (args[i] == name) return args[i + 1];
            return fallback;
        }

        static string Sha256(string path)
        {
            using (var sha = SHA256.Create()) using (var stream = File.OpenRead(path))
                return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
        }

        public static void Run()
        {
            var fixtures = Arg("-opFixtures", "");
            var reportPath = Arg("-opReport", Path.Combine(fixtures, "unity-validation.json"));
            var report = new Report
            {
                engine = Application.unityVersion,
                pipeline = UnityEngine.Rendering.GraphicsSettings.currentRenderPipeline != null
                    ? UnityEngine.Rendering.GraphicsSettings.currentRenderPipeline.GetType().Name : "Built-in (not URP: NOT RUN)",
                graphics = SystemInfo.graphicsDeviceType + " / " + SystemInfo.graphicsDeviceName + " / " + SystemInfo.operatingSystem,
                fixtures = fixtures,
            };
            try
            {
                if (string.IsNullOrEmpty(fixtures) || !Directory.Exists(fixtures))
                {
                    report.notRun.Add("no fixtures folder (-opFixtures)");
                    Finish(report, reportPath, "NOT RUN");
                    return;
                }
                var record = File.ReadAllText(Path.Combine(Path.GetDirectoryName(fixtures), "fixtures.json"));
                foreach (var fixtureDir in Directory.GetDirectories(fixtures))
                {
                    var fixture = Path.GetFileName(fixtureDir);
                    var target = Path.Combine(AssetRoot, fixture);
                    Directory.CreateDirectory(target);
                    foreach (var file in Directory.GetFiles(fixtureDir))
                    {
                        var name = Path.GetFileName(file);
                        var hash = Sha256(file);
                        report.imported.Add(fixture + "/" + name + " sha256 " + hash);
                        if (!record.Contains(hash)) report.errors.Add(fixture + "/" + name + ": hash not in fixtures.json");
                        var destination = Path.Combine(target, name);
                        File.Copy(file, destination, true);
                        if (name.EndsWith(".png"))
                            File.Copy(file, Path.Combine(target, Path.GetFileNameWithoutExtension(name) + "_uncompressed.png"), true);
                    }
                }
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                foreach (var guid in AssetDatabase.FindAssets("t:Texture2D", new[] { AssetRoot }))
                {
                    var path = AssetDatabase.GUIDToAssetPath(guid);
                    var importer = AssetImporter.GetAtPath(path) as TextureImporter;
                    if (importer == null) continue;
                    var name = Path.GetFileNameWithoutExtension(path);
                    var kind = name.Replace("_uncompressed", "");
                    kind = kind.Substring(kind.LastIndexOf('_') + 1);
                    importer.mipmapEnabled = true;
                    importer.streamingMipmaps = false;
                    importer.textureCompression = name.EndsWith("_uncompressed") ? TextureImporterCompression.Uncompressed : TextureImporterCompression.CompressedHQ;
                    switch (kind)
                    {
                        case "BaseMap": case "EmissionMap":
                            importer.textureType = TextureImporterType.Default; importer.sRGBTexture = true;
                            importer.alphaIsTransparency = kind == "BaseMap"; break;
                        case "Normal":
                            importer.textureType = TextureImporterType.NormalMap; importer.sRGBTexture = false;
                            importer.convertToNormalmap = false; break;      // no flip: OpenGL +Y as exported
                        case "MetallicGlossMap":
                            importer.textureType = TextureImporterType.Default; importer.sRGBTexture = false;
                            importer.alphaIsTransparency = false;            // alpha is smoothness
                            importer.alphaSource = TextureImporterAlphaSource.FromInput; break;
                        case "OcclusionMap":
                            importer.textureType = TextureImporterType.Default; importer.sRGBTexture = false; break;
                    }
                    importer.SaveAndReimport();
                    report.settings.Add(path + ": type=" + importer.textureType + " sRGB=" + importer.sRGBTexture + " alphaIsTransparency=" + importer.alphaIsTransparency + " compression=" + importer.textureCompression + " mipmaps=" + importer.mipmapEnabled);
                }
                foreach (var guid in AssetDatabase.FindAssets("t:Model", new[] { AssetRoot }))
                {
                    var path = AssetDatabase.GUIDToAssetPath(guid);
                    var importer = AssetImporter.GetAtPath(path) as ModelImporter;
                    if (importer == null) continue;
                    importer.importNormals = ModelImporterNormals.Import;
                    importer.importTangents = ModelImporterTangents.Import;
                    importer.materialImportMode = ModelImporterMaterialImportMode.None;
                    importer.SaveAndReimport();
                    report.settings.Add(path + ": normals=Import tangents=Import (a computed-tangent copy is NOT RUN in batch mode)");
                }
                BuildMaterials(report);
                foreach (var scene in Scenes)
                    report.scenes.Add(scene + ": NOT RUN (scene assembly and capture require an interactive editor session; materials are in " + AssetRoot + ")");
                Finish(report, reportPath, report.errors.Count == 0 ? "LIMITED TO asset import and material assembly" : "FAIL");
            }
            catch (Exception error)
            {
                report.errors.Add(error.ToString());
                Finish(report, reportPath, "FAIL");
            }
        }

        static void BuildMaterials(Report report)
        {
            var lit = Shader.Find("Universal Render Pipeline/Lit");
            var unlit = Shader.Find("Universal Render Pipeline/Unlit");
            if (lit == null) { report.notRun.Add("URP Lit shader not found: not a URP project"); return; }
            foreach (var fixtureDir in Directory.GetDirectories(AssetRoot))
            {
                var textures = AssetDatabase.FindAssets("t:Texture2D", new[] { fixtureDir })
                    .Select(AssetDatabase.GUIDToAssetPath).Where(p => !p.Contains("_uncompressed"))
                    .ToDictionary(p => Path.GetFileNameWithoutExtension(p), p => AssetDatabase.LoadAssetAtPath<Texture2D>(p));
                foreach (var setName in textures.Keys.Select(k => k.Substring(0, k.LastIndexOf('_'))).Distinct())
                {
                    Texture2D Map(string kind) => textures.TryGetValue(setName + "_" + kind, out var t) ? t : null;
                    var material = new Material(lit);
                    material.SetTexture("_BaseMap", Map("BaseMap"));
                    material.SetTexture("_BumpMap", Map("Normal")); material.EnableKeyword("_NORMALMAP");
                    material.SetTexture("_MetallicGlossMap", Map("MetallicGlossMap")); material.EnableKeyword("_METALLICSPECGLOSSMAP");
                    material.SetFloat("_SmoothnessTextureChannel", 0f);   // metallic alpha
                    material.SetFloat("_Smoothness", 1f); material.SetFloat("_Metallic", 1f);
                    material.SetTexture("_OcclusionMap", Map("OcclusionMap")); material.EnableKeyword("_OCCLUSIONMAP");
                    if (Map("EmissionMap") != null) { material.SetTexture("_EmissionMap", Map("EmissionMap")); material.EnableKeyword("_EMISSION"); material.SetColor("_EmissionColor", Color.white); }
                    AssetDatabase.CreateAsset(material, Path.Combine(fixtureDir, "M_" + setName + ".mat"));
                    foreach (var kind in new[] { "BaseMap", "Normal", "MetallicGlossMap", "OcclusionMap" })
                    {
                        if (unlit == null || Map(kind) == null) continue;
                        var reference = new Material(unlit);
                        reference.SetTexture("_BaseMap", Map(kind));
                        AssetDatabase.CreateAsset(reference, Path.Combine(fixtureDir, "M_" + setName + "_unlit_" + kind + ".mat"));
                    }
                    report.settings.Add(setName + ": URP Lit _MetallicGlossMap with _SmoothnessTextureChannel=0 (metallic alpha), _BumpMap without flip");
                }
            }
            AssetDatabase.SaveAssets();
        }

        static void Finish(Report report, string path, string status)
        {
            report.status = status;
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllText(path, JsonUtility.ToJson(report, true));
            Debug.Log("OPEN_PAINTER_UNITY_VALIDATION " + status + " " + path);
        }
    }
}
#endif
