// Optional macOS browser check for exported portable Material Director reviews.
// Loads one local HTML file into a fresh WebKit data store. No network URLs or
// external resources are accepted. Forces decoding every embedded PNG.
import Cocoa
import WebKit

final class ReviewCheck: NSObject, WKNavigationDelegate {
    let output: URL
    init(_ output: URL) { self.output = output }
    func finish(_ value: [String: Any], _ code: Int32) {
        let data = try! JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys])
        try! data.write(to: output)
        print(String(data: data, encoding: .utf8)!)
        exit(code)
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        finish(["status": "FAIL", "error": String(describing: error)], 1)
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        finish(["status": "FAIL", "error": String(describing: error)], 1)
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        let script = """
        const images = Array.from(document.images);
        for (const image of images) image.loading = 'eager';
        const forbidden = Array.from(document.querySelectorAll('[src],[href]')).map(e => e.getAttribute('src') || e.getAttribute('href')).filter(u => !u.startsWith('data:image/png;base64,') && !u.startsWith('#'));
        const decoded = await Promise.all(images.map(async image => {
          try { await image.decode(); return {width:image.naturalWidth,height:image.naturalHeight,complete:image.complete}; }
          catch (error) { return {error:String(error)}; }
        }));
        return {images:decoded,external_resources:forbidden,document_title:document.title,offline:true};
        """
        webView.callAsyncJavaScript(script, arguments: [:], in: nil, in: .page) { result in
            switch result {
            case .success(let object):
                guard var record = object as? [String: Any], let images = record["images"] as? [[String: Any]],
                      let external = record["external_resources"] as? [String] else {
                    self.finish(["status":"FAIL","error":"Unexpected browser result"], 1); return
                }
                let valid = !images.isEmpty && external.isEmpty && images.allSatisfy { ($0["width"] as? Int)==1024 && ($0["height"] as? Int)==1024 && ($0["complete"] as? Bool)==true }
                record["status"] = valid ? "PASS" : "FAIL"
                record["browser"] = "macOS WKWebView; fresh nonpersistent data store"
                self.finish(record, valid ? 0 : 1)
            case .failure(let error): self.finish(["status":"FAIL","error":String(describing:error)], 1)
            }
        }
    }
}

guard CommandLine.arguments.count == 3 else {
    fputs("Usage: validate_material_review_offline HTML OUTPUT_JSON\n", stderr); exit(2)
}
let html = URL(fileURLWithPath: CommandLine.arguments[1]).standardizedFileURL
let output = URL(fileURLWithPath: CommandLine.arguments[2]).standardizedFileURL
let application = NSApplication.shared
application.setActivationPolicy(.prohibited)
let configuration = WKWebViewConfiguration()
configuration.websiteDataStore = .nonPersistent()
let view = WKWebView(frame: NSRect(x:0,y:0,width:1280,height:900), configuration: configuration)
let delegate = ReviewCheck(output)
view.navigationDelegate = delegate
let timeout = Timer.scheduledTimer(withTimeInterval: 55, repeats: false) { _ in
    delegate.finish(["status":"FAIL","error":"Offline browser validation timed out"], 1)
}
view.loadFileURL(html, allowingReadAccessTo: html.deletingLastPathComponent())
application.run()
