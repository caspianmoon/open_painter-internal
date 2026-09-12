/* SPDX-License-Identifier: GPL-2.0-or-later */
#import <Cocoa/Cocoa.h>
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>

#include "BPY_extern_run.hh"
#include "MEM_guardedalloc.h"
#include "BKE_image.hh"
#include "DNA_image_types.h"
#include "DNA_theme_types.h"
#include "DNA_userdef_types.h"
#include "IMB_imbuf_types.hh"
#include "BKE_context.hh"
#include "BKE_global.hh"
#include "BKE_layer.hh"
#include "BKE_main.hh"
#include "BKE_scene.hh"
#include "BLI_listbase.h"
#include "BLI_math_matrix.h"
#include "BLI_math_vector.h"
#include "BLI_math_geom.h"
#include "DNA_screen_types.h"
#include "DNA_view3d_types.h"
#include "DEG_depsgraph.hh"
#include "DEG_depsgraph_build.hh"
#include "DRW_engine.hh"
#include "ED_view3d_offscreen.hh"
#include "GPU_context.hh"
#include "GPU_framebuffer.hh"
#include "GPU_state.hh"
#include "GPU_viewport.hh"
#include "WM_api.hh"
#include "mtl_context.hh"
#include "mtl_texture.hh"
#include <algorithm>
#include <cstdio>

using namespace blender;

static bTheme *painterTheme() {return static_cast<bTheme *>(U.themes.first);}
static NSColor *painterColor(const unsigned char *rgba) {
  return [NSColor colorWithSRGBRed:rgba[0]/255.0 green:rgba[1]/255.0 blue:rgba[2]/255.0 alpha:1];
}
static NSColor *painterPanelColor() {return painterColor(painterTheme()->tui.panel_back);}
@interface PainterButton : NSButton
@end
@implementation PainterButton
- (void)drawRect:(NSRect)dirtyRect {
  const uiWidgetColors &colors=painterTheme()->tui.wcol_tool;
  NSBezierPath *path=[NSBezierPath bezierPathWithRoundedRect:NSInsetRect(self.bounds,2,2) xRadius:3 yRadius:3];
  [painterColor(self.highlighted?colors.inner_sel:colors.inner) setFill];[path fill];
  [painterColor(colors.outline) setStroke];path.lineWidth=.5;[path stroke];
  NSRect textRect=NSInsetRect(self.bounds,5,5);
  if(self.image){NSRect imageRect=NSMakeRect(8,28,self.bounds.size.width-16,self.bounds.size.height-38);[self.image drawInRect:imageRect fromRect:NSZeroRect operation:NSCompositingOperationSourceOver fraction:1 respectFlipped:YES hints:nil];textRect=NSMakeRect(4,7,self.bounds.size.width-8,18);}
  NSMutableParagraphStyle *paragraph=[[[NSMutableParagraphStyle alloc] init] autorelease];paragraph.alignment=NSTextAlignmentCenter;
  [self.title drawInRect:textRect withAttributes:@{NSFontAttributeName:[NSFont systemFontOfSize:12],NSForegroundColorAttributeName:painterColor(colors.text),NSParagraphStyleAttributeName:paragraph}];
}
@end
@interface PainterSliderCell : NSSliderCell
@end
@implementation PainterSliderCell
- (void)drawBarInside:(NSRect)rect flipped:(BOOL)flipped {
  const uiWidgetColors &colors=painterTheme()->tui.wcol_numslider;
  NSRect bar=NSInsetRect(self.controlView.bounds,1,3);
  NSBezierPath *background=[NSBezierPath bezierPathWithRoundedRect:bar xRadius:3 yRadius:3];[painterColor(colors.inner) setFill];[background fill];
  NSRect fill=bar;fill.size.width*=self.doubleValue;[painterColor(colors.item) setFill];NSRectFill(fill);
  NSString *value=[NSString stringWithFormat:@"%.3f",self.doubleValue];NSSize size=[value sizeWithAttributes:@{NSFontAttributeName:[NSFont systemFontOfSize:11]}];
  [value drawAtPoint:NSMakePoint(NSMidX(bar)-size.width/2,NSMidY(bar)-size.height/2) withAttributes:@{NSFontAttributeName:[NSFont systemFontOfSize:11],NSForegroundColorAttributeName:painterColor(colors.text)}];
}
- (void)drawKnob:(NSRect)rect {}
@end
@interface PainterPopupCell : NSPopUpButtonCell
@end
@implementation PainterPopupCell
- (void)drawWithFrame:(NSRect)frame inView:(NSView *)view {
  const uiWidgetColors &colors=painterTheme()->tui.wcol_menu;
  NSBezierPath *background=[NSBezierPath bezierPathWithRoundedRect:NSInsetRect(frame,1,2) xRadius:3 yRadius:3];[painterColor(colors.inner) setFill];[background fill];[painterColor(colors.outline) setStroke];[background stroke];
  NSDictionary *attributes=@{NSFontAttributeName:[NSFont systemFontOfSize:12],NSForegroundColorAttributeName:painterColor(colors.text)};
  [self.title drawAtPoint:NSMakePoint(frame.origin.x+8,frame.origin.y+7) withAttributes:attributes];
  [@"⌄" drawAtPoint:NSMakePoint(NSMaxX(frame)-18,frame.origin.y+7) withAttributes:attributes];
}
@end
@interface PainterRowView : NSTableRowView
@end
@implementation PainterRowView
- (void)drawSelectionInRect:(NSRect)rect {
  [painterColor(painterTheme()->tui.wcol_list_item.inner_sel) setFill];NSRectFill(self.bounds);
}
@end

// Rendering data only. No Blender editor region is displayed or handles events.
struct PainterRenderState {
  bContext *context;
  Scene *scene;
  View3D *view;
  ARegion *region;
  Depsgraph *depsgraph;
  GPUOffScreen *offscreen = nullptr;
  GPUViewport *viewport = nullptr;
  int width = 0, height = 0;
  float yaw = .6f, pitch = .4f, distance = 6.0f;
};

static bool painterBindScene(PainterRenderState *state)
{
  state->scene=CTX_data_scene(state->context);
  if (!state->scene) state->scene=static_cast<Scene *>(G_MAIN->scenes.first);
  state->view=nullptr; state->region=nullptr;
  for (bScreen &screen : G_MAIN->screens) {
    for (ScrArea &area : screen.areabase) {
    if (area.spacetype==SPACE_VIEW3D && !state->view) {
      state->view=reinterpret_cast<View3D *>(area.spacedata.first);
      for (ARegion &region : area.regionbase) {
      if (region.regiontype==RGN_TYPE_WINDOW) state->region=&region;
      }
    }
    }
  }
  if (!state->view || !state->region) { fprintf(stderr,"Painter requires initial render-view data\n"); return false; }
  state->view->shading.type=OB_MATERIAL;
  state->depsgraph=BKE_scene_ensure_depsgraph(G_MAIN,state->scene,BKE_view_layer_default_view(state->scene));
  DEG_graph_build_from_view_layer(state->depsgraph);
  return true;
}

@class PainterCanvas;
static bool painterPaint3D(PainterCanvas *,NSEvent *,bool);

@interface PainterCanvas : NSView {
 @public
  PainterRenderState *state;
  CAMetalLayer *surface;
  id<MTLRenderPipelineState> pipeline;
  NSTimer *timer;
  bool dirty;
  int settle;
  id paintView;
  NSPoint lastUV;
  int lastFace;
  bool painting;
}
- (void)renderFrame:(NSTimer *)sender;
@end

@implementation PainterCanvas
- (BOOL)acceptsFirstResponder { return YES; }
- (void)mouseDown:(NSEvent *)event {painting=painterPaint3D(self,event,true);}
- (void)mouseDragged:(NSEvent *)event {
  if(painting){painterPaint3D(self,event,false);return;}
  state->yaw += event.deltaX * .008f;
  state->pitch += event.deltaY * .008f;
  dirty = true;
}
- (void)rightMouseDragged:(NSEvent *)event {painting=false;[self mouseDragged:event]; }
- (void)otherMouseDragged:(NSEvent *)event {painting=false;[self mouseDragged:event]; }
- (void)scrollWheel:(NSEvent *)event {
  state->distance = std::clamp(state->distance + float(event.scrollingDeltaY) * .04f, 2.0f, 30.0f);
  dirty = true;
}
- (void)setFrameSize:(NSSize)size { [super setFrameSize:size]; dirty = true; }
- (void)renderFrame:(NSTimer *)sender {
  (void)sender;
  if ((!dirty && settle<=0) || !self.window.visible || self.window.miniaturized) return;
  @autoreleasepool {
    if (dirty) settle=8;

    const double start=CFAbsoluteTimeGetCurrent();
    DRW_gpu_context_enable();
    auto *metal = static_cast<gpu::MTLContext *>(gpu::unwrap(GPU_context_active_get()));
    if (!surface.device) {
      surface.device = metal->device;
      surface.pixelFormat = MTLPixelFormatBGRA8Unorm;
      surface.framebufferOnly = YES;
      NSError *error = nil;
      NSString *source = @"#include <metal_stdlib>\nusing namespace metal;\n"
        "struct V { float4 p [[position]]; float2 uv; };\n"
        "vertex V vs(uint i [[vertex_id]]) { float2 p=float2((i<<1)&2,i&2); V v; v.p=float4(p*2-1,0,1); v.uv=float2(p.x,1-p.y); return v; }\n"
        "fragment float4 fs(V v [[stage_in]], texture2d<float> tex [[texture(0)]]) { constexpr sampler s(filter::linear); return tex.sample(s,v.uv); }";
      id<MTLLibrary> library = [metal->device newLibraryWithSource:source options:nil error:&error];
      MTLRenderPipelineDescriptor *desc = [[MTLRenderPipelineDescriptor alloc] init];
      desc.vertexFunction = [library newFunctionWithName:@"vs"];
      desc.fragmentFunction = [library newFunctionWithName:@"fs"];
      desc.colorAttachments[0].pixelFormat = surface.pixelFormat;
      pipeline = [metal->device newRenderPipelineStateWithDescriptor:desc error:&error];
      [desc.vertexFunction release]; [desc.fragmentFunction release]; [desc release]; [library release];
      if (!pipeline) { fprintf(stderr,"Painter presentation error: %s\n",error.description.UTF8String); dirty=false; DRW_gpu_context_disable(); return; }
    }
    const int width = std::max(64, int(self.bounds.size.width));
    const int height = std::max(64, int(self.bounds.size.height));
    if (width != state->width || height != state->height) {
      if (state->offscreen) GPU_offscreen_free(state->offscreen);
      char error[256] = {};
      state->offscreen = GPU_offscreen_create(width,height,true,gpu::TextureFormat::UNORM_8_8_8_8,GPU_TEXTURE_USAGE_SHADER_READ,true,error);
      state->width=width; state->height=height;
      surface.drawableSize = CGSizeMake(width,height);
      if (!state->offscreen) { fprintf(stderr,"Painter framebuffer: %s\n",error); dirty=false; DRW_gpu_context_disable(); return; }
    }
    if (!state->viewport) state->viewport=GPU_viewport_create();
    GPU_viewport_tag_update(state->viewport);
    DEG_evaluate_on_refresh(state->depsgraph);
    float view[4][4], projection[4][4];
    unit_m4(view); translate_m4(view,0,0,-state->distance);
    rotate_m4(view,'X',state->pitch); rotate_m4(view,'Y',state->yaw);
    const float aspect=float(width)/height;
    perspective_m4(projection,-.025f*aspect,.025f*aspect,-.025f,.025f,.05f,1000.f);
    GPU_offscreen_bind(state->offscreen,true);
    ED_view3d_draw_offscreen(state->depsgraph,state->scene,OB_MATERIAL,state->view,state->region,
      nullptr,width,height,view,projection,false,true,"",true,true,state->offscreen,state->viewport);
    GPU_offscreen_unbind(state->offscreen,true);
    // Submit EEVEE first. Presentation uses the SAME Metal queue: GPU ordering,
    // no read_color(), pixel copy, socket, or waitUntilCompleted() on the CPU.
    GPU_flush();
    id<MTLTexture> texture=static_cast<gpu::MTLTexture *>(GPU_offscreen_color_texture(state->offscreen))->get_metal_handle();
    id<CAMetalDrawable> drawable=[surface nextDrawable];
    if (drawable) {
      MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];
      pass.colorAttachments[0].texture=drawable.texture;
      pass.colorAttachments[0].loadAction=MTLLoadActionDontCare;
      pass.colorAttachments[0].storeAction=MTLStoreActionStore;
      id<MTLCommandBuffer> command=[metal->queue commandBuffer];
      id<MTLRenderCommandEncoder> encoder=[command renderCommandEncoderWithDescriptor:pass];
      [encoder setRenderPipelineState:pipeline]; [encoder setFragmentTexture:texture atIndex:0];
      [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
      [encoder endEncoding]; [command presentDrawable:drawable]; [command commit];
    }
    GPU_render_step();
    DRW_gpu_context_disable();
    dirty=GPU_viewport_do_update(state->viewport); --settle;
    fprintf(stdout,"PAINTER_FRAME_CPU_MS %.2f\n",(CFAbsoluteTimeGetCurrent()-start)*1000);
    fflush(stdout);
    static bool reported=false;
    if (!reported) { puts("PAINTER_CUSTOM_WINDOW_EEVEE_FRAME"); fflush(stdout); reported=true; }
  }
}
@end

@interface PainterUV : NSView {
 @public
  Image *image;
  PainterCanvas *canvas;
  NSColorWell *brushColor;
  NSPoint previous;
  float brushRadius;
}
@end
@implementation PainterUV
- (BOOL)isFlipped {return NO;}
- (NSRect)imageRect {CGFloat side=MIN(self.bounds.size.width,self.bounds.size.height)-28;return NSMakeRect((self.bounds.size.width-side)/2,(self.bounds.size.height-side)/2,side,side);}
- (void)drawRect:(NSRect)rect {
  [[NSColor colorWithWhite:.12 alpha:1] setFill];NSRectFill(self.bounds);
  [@"2D TEXTURE · Paint with left mouse" drawAtPoint:NSMakePoint(12,self.bounds.size.height-28) withAttributes:@{NSForegroundColorAttributeName:[NSColor lightGrayColor]}];
  if (!image) {[@"Select a paint or image layer" drawAtPoint:NSMakePoint(12,30) withAttributes:@{NSForegroundColorAttributeName:[NSColor lightGrayColor]}];return;}
  void *lock=nullptr;ImBuf *buffer=BKE_image_acquire_ibuf(image,nullptr,&lock);
  if(buffer && buffer->byte_buffer.data) {
    NSRect target=[self imageRect];[[NSColor colorWithWhite:.3 alpha:1] setFill];NSRectFill(target);
    unsigned char *planes[]={const_cast<unsigned char *>(buffer->byte_data())};
    NSBitmapImageRep *rep=[[NSBitmapImageRep alloc] initWithBitmapDataPlanes:planes pixelsWide:buffer->x pixelsHigh:buffer->y bitsPerSample:8 samplesPerPixel:4 hasAlpha:YES isPlanar:NO colorSpaceName:NSDeviceRGBColorSpace bitmapFormat:NSBitmapFormatAlphaNonpremultiplied bytesPerRow:buffer->x*4 bitsPerPixel:32];
    [NSGraphicsContext saveGraphicsState];NSAffineTransform *flip=[NSAffineTransform transform];[flip translateXBy:0 yBy:2*target.origin.y+target.size.height];[flip scaleXBy:1 yBy:-1];[flip concat];
    [rep drawInRect:target];[NSGraphicsContext restoreGraphicsState];[rep release];
  }
  BKE_image_release_ibuf(image,buffer,lock);
}
- (void)stampFrom:(NSPoint)start to:(NSPoint)end erase:(BOOL)erase {
  if(!image)return;
  NSRect target=[self imageRect];
  void *lock=nullptr;ImBuf *buffer=BKE_image_acquire_ibuf(image,nullptr,&lock);
  if(buffer && buffer->byte_buffer.data) {
    float ax=(start.x-target.origin.x)/target.size.width*buffer->x,ay=(start.y-target.origin.y)/target.size.height*buffer->y;
    float bx=(end.x-target.origin.x)/target.size.width*buffer->x,by=(end.y-target.origin.y)/target.size.height*buffer->y;
    unsigned char *pixels=buffer->byte_data_for_write();
    const float radius=brushRadius>0?brushRadius:12;int steps=std::max(1,int(hypotf(bx-ax,by-ay)/3));
    NSColor *c=[brushColor.color colorUsingColorSpace:[NSColorSpace sRGBColorSpace]];
    float rgb[]={float(c.redComponent),float(c.greenComponent),float(c.blueComponent)};
    rcti changed={buffer->x,0,buffer->y,0};
    for(int step=0;step<=steps;step++) {
      float x=ax+(bx-ax)*step/steps,y=ay+(by-ay)*step/steps;
      int xmin=std::max(0,int(x-radius)),xmax=std::min(buffer->x,int(x+radius)+1),ymin=std::max(0,int(y-radius)),ymax=std::min(buffer->y,int(y+radius)+1);
      for(int py=ymin;py<ymax;py++)for(int px=xmin;px<xmax;px++) {
        float coverage=std::clamp(radius-hypotf(px-x,py-y),0.f,1.f);if(coverage<=0)continue;
        unsigned char *pixel=pixels+4*(py*buffer->x+px);
        float alpha=pixel[3]/255.f,out=coverage+alpha*(1-coverage);
        if(erase)pixel[3]=uint8_t(alpha*(1-coverage)*255);
        else {for(int ch=0;ch<3;ch++)pixel[ch]=uint8_t((rgb[ch]*coverage+pixel[ch]/255.f*alpha*(1-coverage))/out*255);pixel[3]=uint8_t(out*255);}
      }
      changed.xmin=std::min(changed.xmin,xmin);changed.xmax=std::max(changed.xmax,xmax);changed.ymin=std::min(changed.ymin,ymin);changed.ymax=std::max(changed.ymax,ymax);
    }
    if(changed.xmax>changed.xmin && changed.ymax>changed.ymin) {
      BKE_image_mark_dirty(image,buffer);
      BKE_image_partial_update_mark_region(image,static_cast<ImageTile *>(image->tiles.first),buffer,&changed);
      canvas->dirty=true;[self setNeedsDisplay:YES];
    }
  }
  BKE_image_release_ibuf(image,buffer,lock);
}
- (void)restoreStroke:(NSDictionary *)snapshot {
  NSString *name=snapshot[@"name"];Image *target=nullptr;
  for(Image &candidate : G_MAIN->images)if([name isEqualToString:[NSString stringWithUTF8String:candidate.id.name+2]]){target=&candidate;break;}
  if(!target)return;
  void *lock=nullptr;ImBuf *buffer=BKE_image_acquire_ibuf(target,nullptr,&lock);NSData *data=snapshot[@"pixels"];
  if(buffer && data.length==size_t(buffer->x)*buffer->y*4 && buffer->byte_data()) {
    NSDictionary *inverse=@{@"name":name,@"pixels":[NSData dataWithBytes:buffer->byte_data() length:data.length]};
    [[self.window.undoManager prepareWithInvocationTarget:self] restoreStroke:inverse];
    memcpy(buffer->byte_data_for_write(),data.bytes,data.length);
    BKE_image_mark_dirty(target,buffer);BKE_image_partial_update_mark_full_update(target);canvas->dirty=true;[self setNeedsDisplay:YES];
  }
  BKE_image_release_ibuf(target,buffer,lock);
}
- (void)beginStroke {
  if(!image)return;
  void *lock=nullptr;ImBuf *buffer=BKE_image_acquire_ibuf(image,nullptr,&lock);
  if(buffer && buffer->byte_data()) {
    NSDictionary *snapshot=@{@"name":[NSString stringWithUTF8String:image->id.name+2],@"pixels":[NSData dataWithBytes:buffer->byte_data() length:size_t(buffer->x)*buffer->y*4]};
    NSUndoManager *undo=self.window.undoManager;undo.levelsOfUndo=8;
    [[undo prepareWithInvocationTarget:self] restoreStroke:snapshot];[undo setActionName:@"Paint stroke"];
  }
  BKE_image_release_ibuf(image,buffer,lock);
}
- (void)mouseDown:(NSEvent *)event {[self beginStroke];previous=[self convertPoint:event.locationInWindow fromView:nil];if(NSPointInRect(previous,[self imageRect]))[self stampFrom:previous to:previous erase:(event.modifierFlags&NSEventModifierFlagShift)!=0];}
- (void)mouseDragged:(NSEvent *)event {NSPoint point=[self convertPoint:event.locationInWindow fromView:nil];[self stampFrom:previous to:point erase:(event.modifierFlags&NSEventModifierFlagShift)!=0];previous=point;}
@end

static NSDictionary *painterRequest(bContext *context, NSDictionary *request)
{
  NSData *data=[NSJSONSerialization dataWithJSONObject:request options:0 error:nil];
  NSString *encoded=[data base64EncodedStringWithOptions:0];
  NSString *expression=[NSString stringWithFormat:@"open_painter_native.shell_dispatch('%@')",encoded];
  const char *imports[]={"open_painter_native",nullptr};
  char *result=nullptr;
  if (!BPY_run_string_as_string(context,imports,expression.UTF8String,nullptr,&result)) return nil;
  NSData *response=[NSData dataWithBytes:result length:strlen(result)];
  MEM_delete(result);
  return [NSJSONSerialization JSONObjectWithData:response options:0 error:nil];
}

static bool painterPaint3D(PainterCanvas *canvas,NSEvent *event,bool first)
{
  PainterUV *uv=(PainterUV *)canvas->paintView;
  if(!uv || !uv->image || (event.modifierFlags & NSEventModifierFlagOption))return false;
  if(first)[uv beginStroke];
  NSPoint point=[canvas convertPoint:event.locationInWindow fromView:nil];
  float view[4][4],projection[4][4],combined[4][4],inverse[4][4];
  auto *s=canvas->state;unit_m4(view);translate_m4(view,0,0,-s->distance);rotate_m4(view,'X',s->pitch);rotate_m4(view,'Y',s->yaw);
  float aspect=canvas.bounds.size.width/canvas.bounds.size.height;
  perspective_m4(projection,-.025f*aspect,.025f*aspect,-.025f,.025f,.05f,1000.f);mul_m4_m4m4(combined,projection,view);invert_m4_m4(inverse,combined);
  float near[3]={float(point.x/canvas.bounds.size.width*2-1),float(point.y/canvas.bounds.size.height*2-1),-1},far[3]={near[0],near[1],1},direction[3];
  mul_project_m4_v3(inverse,near);mul_project_m4_v3(inverse,far);sub_v3_v3v3(direction,far,near);normalize_v3(direction);
  NSDictionary *hit=painterRequest(s->context,@{@"action":@"ray",@"origin":@[@(near[0]),@(near[1]),@(near[2])],@"direction":@[@(direction[0]),@(direction[1]),@(direction[2])]});
  if(![hit[@"hit"] boolValue]) {canvas->lastFace=-1;return true;}
  NSArray *coord=hit[@"uv"];NSRect rect=[uv imageRect];NSPoint target=NSMakePoint(rect.origin.x+[coord[0] doubleValue]*rect.size.width,rect.origin.y+[coord[1] doubleValue]*rect.size.height);
  int face=[hit[@"face"] intValue];
  // Never connect across UV seams or empty space.
  NSPoint start=(!first && canvas->lastFace==face)?canvas->lastUV:target;
  [uv stampFrom:start to:target erase:(event.modifierFlags&NSEventModifierFlagShift)!=0];
  canvas->lastUV=target;canvas->lastFace=face;return true;
}

@interface PainterControls : NSObject <NSTableViewDataSource,NSTableViewDelegate> {
 @public
  PainterCanvas *canvas;
  PainterUV *uv;
  NSView *root,*left,*right;
  NSTableView *table;
  NSTextField *setName,*layerName,*status;
  NSColorWell *color;
  NSSlider *opacity,*roughness,*metallic;
  NSPopUpButton *blend,*mask,*filter,*wrap;
  NSButton *visibility;
  NSArray *rows;
  BOOL refreshing;
}
- (void)refresh;
- (void)action:(id)sender;
- (void)valueChanged:(id)sender;
- (void)layout;
@end

static NSTextField *painterLabel(NSView *view,NSString *text,NSRect frame)
{
  NSTextField *label=[NSTextField labelWithString:text];label.frame=frame;
  label.font=[NSFont systemFontOfSize:12];label.textColor=painterColor(painterTheme()->tui.wcol_regular.text);[view addSubview:label];return label;
}
static NSButton *painterButton(NSView *view,NSString *title,NSString *key,id target,NSRect frame)
{
  NSButton *button=[[[PainterButton alloc] initWithFrame:frame] autorelease];button.title=title;button.target=target;button.action=@selector(action:);
  button.identifier=key;button.frame=frame;button.bezelStyle=NSBezelStyleRounded;
  [view addSubview:button];return button;
}
@implementation PainterControls
- (void)layout {
  CGFloat w=root.bounds.size.width,h=root.bounds.size.height;
  CGFloat l=left.hidden?0:230,r=right.hidden?0:320;
  left.frame=NSMakeRect(0,0,230,h-44);right.frame=NSMakeRect(w-320,0,320,h-44);
  canvas.frame=NSMakeRect(l,0,MAX(64,(w-l-r)/2),h-44);
  uv.frame=NSMakeRect(l+canvas.frame.size.width,0,MAX(64,(w-l-r)/2),h-44);
}
- (void)refresh {
  NSDictionary *data=painterRequest(canvas->state->context,@{@"action":@"state"});
  if (!data) {status.stringValue=@"Operation failed — see application log";return;}
  refreshing=YES;
  [rows release];rows=[data[@"rows"] retain];[table reloadData];
  setName.stringValue=[data[@"material"] length]?data[@"material"]:@"No texture set";
  NSDictionary *active=nil;NSInteger row=0;
  for (NSDictionary *item in rows) {if ([item[@"index"] isEqual:data[@"active"]]) {active=item;break;}++row;}
  [table selectRowIndexes:active?[NSIndexSet indexSetWithIndex:row]:[NSIndexSet indexSet] byExtendingSelection:NO];
  for (NSControl *control in @[layerName,color,opacity,roughness,metallic,blend,mask,filter,wrap,visibility]) control.enabled=active!=nil;
  if (active) {
    layerName.stringValue=active[@"name"];
    opacity.doubleValue=[active[@"opacity"] doubleValue];roughness.doubleValue=[active[@"roughness"] doubleValue];metallic.doubleValue=[active[@"metallic"] doubleValue];
    NSArray *c=active[@"color"];color.color=[NSColor colorWithSRGBRed:[c[0] doubleValue] green:[c[1] doubleValue] blue:[c[2] doubleValue] alpha:1];
    visibility.state=[active[@"visible"] boolValue]?NSControlStateValueOn:NSControlStateValueOff;
    for (NSPopUpButton *popup in @[blend,mask,filter,wrap]) {
      for (NSMenuItem *item in popup.itemArray) if ([item.representedObject isEqual:active[popup.identifier]]) [popup selectItem:item];
    }
  }
  uv->image=nullptr;
  if(active && [active[@"image"] length]) {
    for(Image &image : G_MAIN->images) if([active[@"image"] isEqualToString:[NSString stringWithUTF8String:image.id.name+2]]) {uv->image=&image;break;}
  }
  [uv setNeedsDisplay:YES];
  refreshing=NO;
}
- (NSTableRowView *)tableView:(NSTableView *)table rowViewForRow:(NSInteger)row {return [[[PainterRowView alloc] initWithFrame:NSZeroRect] autorelease];}
- (NSInteger)numberOfRowsInTableView:(NSTableView *)view {return rows.count;}
- (id)tableView:(NSTableView *)view objectValueForTableColumn:(NSTableColumn *)column row:(NSInteger)row {
  NSDictionary *item=rows[row];
  NSString *indent=[@"" stringByPaddingToLength:[item[@"depth"] integerValue]*3 withString:@" " startingAtIndex:0];
  return [NSString stringWithFormat:@"%@%@ %@  %@  %.0f%%",indent,[item[@"kind"] isEqual:@"GROUP"]?([item[@"expanded"] boolValue]?@"▾ ▣":@"▸ ▣"):@"◼",item[@"name"],[item[@"mask"] isEqual:@"NONE"]?@"":@"◩",[item[@"opacity"] doubleValue]*100];
}
- (void)tableViewSelectionDidChange:(NSNotification *)notification {
  if (refreshing || table.selectedRow<0) return;
  painterRequest(canvas->state->context,@{@"action":@"select",@"index":rows[table.selectedRow][@"index"]});[self refresh];
}
- (void)action:(id)sender {
  NSString *key=[sender identifier];NSDictionary *request=nil;
  if ([key isEqual:@"new"]) request=@{@"action":@"new"};
  else if ([key isEqual:@"fill"] || [key isEqual:@"group"] || [key isEqual:@"paint"]) request=@{@"action":@"layer",@"kind":[key isEqual:@"group"]?@"GROUP":([key isEqual:@"paint"]?@"PAINT":@"FILL")};
  else if ([key hasPrefix:@"asset"]) request=@{@"action":@"asset",@"index":@([[key substringFromIndex:5] intValue])};
  else if ([key isEqual:@"duplicate"] || [key isEqual:@"delete"]) request=@{@"action":key};
  else if ([key isEqual:@"collapse"] && table.selectedRow>=0) request=@{@"action":@"set",@"key":@"expanded",@"value":@(![rows[table.selectedRow][@"expanded"] boolValue])};
  else if ([key isEqual:@"open"]) {
    NSOpenPanel *panel=[NSOpenPanel openPanel];panel.canChooseDirectories=NO;panel.allowedFileTypes=@[@"blend"];panel.title=@"Open Blender Project";
    if ([panel runModal]==NSModalResponseOK) {
      auto *s=canvas->state;
      DRW_gpu_context_enable();if(s->viewport)GPU_viewport_free(s->viewport);if(s->offscreen)GPU_offscreen_free(s->offscreen);DRW_gpu_context_disable();
      [root.window.undoManager removeAllActions];
      s->viewport=nullptr;s->offscreen=nullptr;s->width=s->height=0;uv->image=nullptr;
      NSDictionary *result=painterRequest(s->context,@{@"action":@"open",@"path":panel.URL.path});
      if(!painterBindScene(s)){status.stringValue=@"File has no render view data";return;}
      canvas->dirty=true;[self refresh];status.stringValue=result?@"Opened":@"Could not open file";
    }
    return;
  }
  else if ([key isEqual:@"save"]) {
    NSSavePanel *panel=[NSSavePanel savePanel];panel.nameFieldStringValue=@"Painting.blend";
    if ([panel runModal]==NSModalResponseOK) request=@{@"action":@"save",@"path":panel.URL.path};
  }
  else if ([key isEqual:@"image"] || [key isEqual:@"bitmap"]) {
    NSOpenPanel *panel=[NSOpenPanel openPanel];panel.canChooseDirectories=NO;
    if ([panel runModal]==NSModalResponseOK) request=@{@"action":@"image",@"path":panel.URL.path,@"mask":@([key isEqual:@"bitmap"])};
  }
  if (request) {
    NSDictionary *result=painterRequest(canvas->state->context,request);
    status.stringValue=result?@"Ready":@"Operation failed — see application log";
    canvas->dirty=true;[self refresh];
  }
}
- (void)valueChanged:(id)sender {
  if (refreshing) return;
  NSString *key=[sender identifier];id value=nil;
  if (sender==color) {NSColor *c=[color.color colorUsingColorSpace:[NSColorSpace sRGBColorSpace]];value=@[@(c.redComponent),@(c.greenComponent),@(c.blueComponent),@1];}
  else if (sender==layerName) value=layerName.stringValue;
  else if (sender==visibility) value=@(visibility.state==NSControlStateValueOn);
  else if ([sender isKindOfClass:[NSPopUpButton class]]) value=[[sender selectedItem] representedObject];
  else value=@([sender doubleValue]);
  painterRequest(canvas->state->context,@{@"action":@"set",@"key":key,@"value":value});
  canvas->dirty=true;
  // Continuous sliders do not rebuild the table or interrupt the tracking loop.
}
- (void)dealloc {[rows release];[super dealloc];}
@end

static PainterControls *painterBuildControls(NSView *root,PainterCanvas *canvas)
{
  PainterControls *c=[[PainterControls alloc] init];c->root=root;c->canvas=canvas;
  const CGFloat h=root.bounds.size.height;
  c->left=[[NSView alloc] initWithFrame:NSMakeRect(0,0,230,h-44)];
  c->right=[[NSView alloc] initWithFrame:NSMakeRect(root.bounds.size.width-320,0,320,h-44)];
  for (NSView *panel in @[c->left,c->right]) {panel.wantsLayer=YES;panel.layer.backgroundColor=painterPanelColor().CGColor;[root addSubview:panel];}
  c->left.autoresizingMask=NSViewHeightSizable;c->right.autoresizingMask=NSViewMinXMargin|NSViewHeightSizable;
  painterLabel(c->left,@"ASSETS",NSMakeRect(14,h-82,200,24)).autoresizingMask=NSViewMinYMargin;
  for (int i=0;i<4;i++) {
    NSArray *names=@[@"Steel",@"Copper",@"Rubber",@"Porcelain"];
    NSButton *b=painterButton(c->left,names[i],[NSString stringWithFormat:@"asset%d",i],c,NSMakeRect(12+(i%2)*108,h-210-(i/2)*120,102,108));
    b.autoresizingMask=NSViewMinYMargin;
    NSString *path=[[[NSBundle mainBundle] resourcePath] stringByAppendingPathComponent:[NSString stringWithFormat:@"5.2/scripts/startup/open_painter_previews/%@.png",names[i]]];
    NSImage *preview=[[NSImage alloc] initWithContentsOfFile:path];
    if(preview){preview.size=NSMakeSize(82,82);b.image=preview;b.imagePosition=NSImageAbove;b.bezelStyle=NSBezelStyleRegularSquare;b.imageScaling=NSImageScaleProportionallyDown;[preview release];}
  }
  painterLabel(c->left,@"Click a material to add a fill layer",NSMakeRect(12,42,215,24));
  c->status=painterLabel(c->left,@"Ready",NSMakeRect(12,12,215,24));
  painterLabel(c->right,@"TEXTURE SET LIST",NSMakeRect(12,h-82,290,24)).autoresizingMask=NSViewMinYMargin;
  c->setName=painterLabel(c->right,@"No texture set",NSMakeRect(12,h-114,205,24));c->setName.autoresizingMask=NSViewMinYMargin;
  painterButton(c->right,@"+ New",@"new",c,NSMakeRect(224,h-115,85,28)).autoresizingMask=NSViewMinYMargin;
  painterLabel(c->right,@"LAYERS",NSMakeRect(12,h-162,290,24)).autoresizingMask=NSViewMinYMargin;
  NSArray *titles=@[@"Paint",@"Fill",@"Folder",@"Copy",@"Delete"],*keys=@[@"paint",@"fill",@"group",@"duplicate",@"delete"];
  for(int i=0;i<5;i++)painterButton(c->right,titles[i],keys[i],c,NSMakeRect(8+i*61,h-196,61,28)).autoresizingMask=NSViewMinYMargin;
  NSScrollView *scroll=[[NSScrollView alloc] initWithFrame:NSMakeRect(8,h-396,304,192)];scroll.hasVerticalScroller=YES;scroll.autoresizingMask=NSViewMinYMargin;
  c->table=[[NSTableView alloc] initWithFrame:scroll.bounds];
  NSTableColumn *column=[[NSTableColumn alloc] initWithIdentifier:@"layers"];column.width=300;[c->table addTableColumn:column];[column release];
  c->table.backgroundColor=painterColor(painterTheme()->space_outliner.back);c->table.headerView=nil;c->table.rowHeight=36;c->table.dataSource=c;c->table.delegate=c;
  c->table.target=c;c->table.doubleAction=@selector(action:);c->table.identifier=@"collapse";
  scroll.documentView=c->table;[c->right addSubview:scroll];[c->table release];[scroll release];
  painterLabel(c->right,@"PROPERTIES",NSMakeRect(12,326,290,24));
  c->layerName=[[NSTextField alloc] initWithFrame:NSMakeRect(12,294,225,26)];c->layerName.identifier=@"name";[c->right addSubview:c->layerName];[c->layerName release];
  c->visibility=[NSButton checkboxWithTitle:@"Visible" target:c action:@selector(valueChanged:)];c->visibility.frame=NSMakeRect(239,294,80,26);c->visibility.identifier=@"visible";[c->right addSubview:c->visibility];
  NSArray *labels=@[@"Opacity",@"Roughness",@"Metallic"];
  for(int i=0;i<3;i++) {
    painterLabel(c->right,labels[i],NSMakeRect(12,257-i*30,94,24));
    NSSlider *slider=[NSSlider sliderWithValue:.5 minValue:0 maxValue:1 target:c action:@selector(valueChanged:)];slider.frame=NSMakeRect(110,257-i*30,194,24);slider.cell=[[[PainterSliderCell alloc] init] autorelease];slider.minValue=0;slider.maxValue=1;slider.target=c;slider.action=@selector(valueChanged:);slider.continuous=YES;
    slider.identifier=@[@"opacity",@"roughness",@"metallic"][i];[c->right addSubview:slider];
    if(i==0)c->opacity=slider;else if(i==1)c->roughness=slider;else c->metallic=slider;
  }
  painterLabel(c->right,@"Base color",NSMakeRect(12,164,100,24));
  c->color=[[NSColorWell alloc] initWithFrame:NSMakeRect(110,164,80,26)];c->color.identifier=@"color";[c->right addSubview:c->color];[c->color release];
  NSArray *options=@[@[@"MIX",@"MULTIPLY",@"SCREEN",@"OVERLAY",@"ADD",@"SUBTRACT"],@[@"NONE",@"WHITE",@"BLACK",@"BITMAP"],@[@"Linear",@"Closest",@"Cubic"],@[@"REPEAT",@"EXTEND",@"CLIP"]];
  for(int i=0;i<4;i++) {
    NSPopUpButton *popup=[[NSPopUpButton alloc] initWithFrame:NSMakeRect(12+(i%2)*150,126-(i/2)*32,144,28) pullsDown:NO];
    popup.cell=[[[PainterPopupCell alloc] initTextCell:@"" pullsDown:NO] autorelease];
    popup.identifier=@[@"blend",@"mask",@"filtering",@"wrap"][i];
    for(NSString *value in options[i]) {[popup addItemWithTitle:value];popup.lastItem.representedObject=value;}
    [c->right addSubview:popup];if(i==0)c->blend=popup;else if(i==1)c->mask=popup;else if(i==2)c->filter=popup;else c->wrap=popup;[popup release];
  }
  painterButton(c->right,@"Load texture",@"image",c,NSMakeRect(12,48,144,28));
  painterButton(c->right,@"Load bitmap mask",@"bitmap",c,NSMakeRect(162,48,146,28));
  painterButton(c->right,@"Open .blend…",@"open",c,NSMakeRect(12,12,144,28));
  painterButton(c->right,@"Save .blend…",@"save",c,NSMakeRect(162,12,146,28));
  for(NSControl *control in @[c->layerName,c->color,c->blend,c->mask,c->filter,c->wrap]) {control.target=c;control.action=@selector(valueChanged:);}
  c->uv=[[PainterUV alloc] initWithFrame:NSZeroRect];c->uv->canvas=canvas;c->uv->brushColor=c->color;canvas->paintView=c->uv;[root addSubview:c->uv];[c->uv release];
  [c layout];[c refresh];[c->left release];[c->right release];return c;
}

@interface PainterWindowDelegate : NSObject <NSWindowDelegate, NSApplicationDelegate> { @public PainterControls *controls; }
@end
@implementation PainterWindowDelegate
- (void)windowDidResize:(NSNotification *)notification {if(controls)[controls layout];}
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)app { [app stop:nil]; return NSTerminateCancel; }
- (void)windowWillClose:(NSNotification *)notification { (void)notification; [NSApp stop:nil]; }
@end

namespace blender {
void painter_shell_run(bContext *context)
{
  @autoreleasepool {
    WM_init_gpu();
    PainterRenderState state{};
    state.context=context;
    if(!painterBindScene(&state))return;
    [NSApplication sharedApplication];
    [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
    NSImage *appIcon=[[NSImage alloc] initWithContentsOfFile:[[[NSBundle mainBundle] resourcePath] stringByAppendingPathComponent:@"OpenPainter.icns"]];
    if(appIcon){NSApp.applicationIconImage=appIcon;[appIcon release];}
    NSMenu *menu=[[NSMenu alloc] init];
    NSMenuItem *appItem=[[NSMenuItem alloc] init]; [menu addItem:appItem];
    NSMenu *appMenu=[[NSMenu alloc] initWithTitle:@"Open Painter"];
    [appMenu addItemWithTitle:@"Quit Open Painter" action:@selector(terminate:) keyEquivalent:@"q"];
    appItem.submenu=appMenu; NSApp.mainMenu=menu;
    NSWindow *window=[[NSWindow alloc] initWithContentRect:NSMakeRect(100,100,1440,900)
      styleMask:NSWindowStyleMaskTitled|NSWindowStyleMaskClosable|NSWindowStyleMaskResizable|NSWindowStyleMaskMiniaturizable
      backing:NSBackingStoreBuffered defer:NO];
    window.minSize=NSMakeSize(1100,880);
    window.title=@"Open Painter"; window.releasedWhenClosed=NO;
    window.appearance=[NSAppearance appearanceNamed:NSAppearanceNameDarkAqua];
    PainterWindowDelegate *delegate=[[PainterWindowDelegate alloc] init]; window.delegate=delegate;
    id oldDelegate=[NSApp.delegate retain]; NSApp.delegate=delegate;
    NSView *root=[[NSView alloc] initWithFrame:NSMakeRect(0,0,1440,900)];
    window.contentView=root;
    NSTextField *title=[NSTextField labelWithString:@"Paint · Shift: erase · Option-drag: orbit · Scroll: zoom · T / N: panels"];
    title.frame=NSMakeRect(310,868,1050,24); title.autoresizingMask=NSViewMinYMargin|NSViewWidthSizable;
    [root addSubview:title];
    PainterCanvas *canvas=[[PainterCanvas alloc] initWithFrame:NSMakeRect(0,0,1100,716)];
    canvas->state=&state; canvas->dirty=true;
    canvas.autoresizingMask=NSViewWidthSizable|NSViewHeightSizable;
    canvas.wantsLayer=YES; canvas->surface=[CAMetalLayer layer]; canvas.layer=canvas->surface;
    [root addSubview:canvas];
    PainterControls *controls=painterBuildControls(root,canvas);delegate->controls=controls;
    painterButton(root,@"Open .blend…",@"open",controls,NSMakeRect(12,864,142,30)).autoresizingMask=NSViewMinYMargin;
    painterButton(root,@"Save .blend…",@"save",controls,NSMakeRect(160,864,142,30)).autoresizingMask=NSViewMinYMargin;
    NSMenuItem *fileItem=[[[NSMenuItem alloc] initWithTitle:@"File" action:nil keyEquivalent:@""] autorelease];
    NSMenu *fileMenu=[[[NSMenu alloc] initWithTitle:@"File"] autorelease];
    for(NSArray *entry in @[@[@"Open .blend…",@"open",@"o"],@[@"Save .blend…",@"save",@"s"]]){
      NSMenuItem *item=[fileMenu addItemWithTitle:entry[0] action:@selector(action:) keyEquivalent:entry[2]];item.identifier=entry[1];item.target=controls;
    }
    fileItem.submenu=fileMenu;[menu addItem:fileItem];
    id keyMonitor=[NSEvent addLocalMonitorForEventsMatchingMask:NSEventMaskKeyDown handler:^NSEvent *(NSEvent *event) {
      if([window.firstResponder isKindOfClass:[NSTextView class]])return event;
      NSString *key=event.charactersIgnoringModifiers.lowercaseString;
      if([key isEqual:@"z"] && (event.modifierFlags & NSEventModifierFlagCommand)) {
        if(event.modifierFlags & NSEventModifierFlagShift)[window.undoManager redo];else [window.undoManager undo];return nil;
      }
      if([key isEqual:@"t"]){controls->left.hidden=!controls->left.hidden;[controls layout];return nil;}
      if([key isEqual:@"n"]){controls->right.hidden=!controls->right.hidden;[controls layout];return nil;}
      return event;
    }];
    if (getenv("PAINTER_TEST_UI")) {
      painterRequest(context,@{@"action":@"asset",@"index":@0});
      painterRequest(context,@{@"action":@"layer",@"kind":@"GROUP"});
      painterRequest(context,@{@"action":@"layer",@"kind":@"PAINT"});
      [controls refresh];
      controls->color.color=[NSColor colorWithSRGBRed:.05 green:.65 blue:1 alpha:1];
      NSRect rect=[controls->uv imageRect];
      const double stampStart=CFAbsoluteTimeGetCurrent();
      [controls->uv stampFrom:NSMakePoint(NSMinX(rect)+rect.size.width*.2,NSMidY(rect)) to:NSMakePoint(NSMinX(rect)+rect.size.width*.8,NSMidY(rect)) erase:NO];
      fprintf(stdout,"PAINTER_STROKE_CPU_MS %.2f\n",(CFAbsoluteTimeGetCurrent()-stampStart)*1000);
      painterRequest(context,@{@"action":@"save",@"path":@"/Volumes/SSD/projects/open_painter/native/logs/ui-painted.blend"});
    }
    [window center]; [window makeKeyAndOrderFront:nil]; [NSApp activateIgnoringOtherApps:YES];
    canvas->timer=[NSTimer scheduledTimerWithTimeInterval:1.0/60 target:canvas selector:@selector(renderFrame:) userInfo:nil repeats:YES];
    [[NSRunLoop mainRunLoop] addTimer:canvas->timer forMode:NSRunLoopCommonModes];
    puts("PAINTER_CUSTOM_WINDOW_OPEN_NO_WM_MAIN"); fflush(stdout);
    [NSApp run];
    [canvas->timer invalidate];
    NSApp.delegate=oldDelegate; [oldDelegate release];
    DRW_gpu_context_enable();
    if (state.viewport) GPU_viewport_free(state.viewport);
    if (state.offscreen) GPU_offscreen_free(state.offscreen);
    DRW_gpu_context_disable();
    [NSEvent removeMonitor:keyMonitor];delegate->controls=nullptr;
    [controls release];
    [canvas->pipeline release]; [canvas release]; [root release]; [window release]; [delegate release];
    [appMenu release]; [appItem release]; [menu release];
  }
}
}
