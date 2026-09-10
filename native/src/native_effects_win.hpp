// Included after the bridge's checked-memory helpers. Exact supplied tier0
// fingerprints gate these private ABI calls; never fall back to guessed slots.
constexpr char kTier0Hash[]="b4300eb0abfe73e1e877516ab6b8bdd1a1bdb4ffc47c7515a349d0623b852f69";
constexpr char kUpdatedTier0Hash[]="b3192eac3cb8c54ac3f9c7aaf7c725ddfcc2dc46d99ba13d16177b6ebf736ebc";
constexpr std::uintptr_t kCvarTable=0x3106e8,kCvarSet=0x20ec60;
std::uintptr_t gTier0=0,gCvar=0;
bool gExtendedCvarSupported=false;
struct CvarRef { std::uint64_t id=0xffffffff; std::uintptr_t data=0; };
static_assert(sizeof(CvarRef)==16,"Verified tier0 ConVarRefAbstract layout");
using FindCvarFn=void*(__fastcall*)(void*,std::uint64_t*,const char*,int);
using CvarDataFn=std::uintptr_t(__fastcall*)(void*,std::uint64_t);
using SetCvarFn=void(__fastcall*)(CvarRef*,int,const void*,void*,void*);
FindCvarFn gFindCvar=nullptr;
CvarDataFn gGetCvarData=nullptr;
SetCvarFn gSetCvar=nullptr;

// CVValue_t type 13 is a contiguous float32 Vector4 in the updated tier0
// build. The older scalar profile does not opt into vector writes. All four lanes are passed through SetValueInternal in one call.
using EffectValue=std::array<double,4>;
struct EffectBinding {
 CvarRef ref;unsigned id=0;EffectValue restore{};
 bool check() const noexcept {
  if(id>=7&&!gExtendedCvarSupported)return false;
  std::uint16_t type=0;std::uint64_t flags=0;std::uintptr_t name=0;
  char text[96]{};const auto& spec=kEffects[id];auto length=std::strlen(spec.name)+1;
  return read_value(ref.data,name)&&read_memory(name,text,length)&&!std::strcmp(text,spec.name)
   &&read_value(ref.data+0x28,type)&&type==spec.type&&read_value(ref.data+0x30,flags)
   // Reject references, per-user/server-controlled vars and callback reentry.
   &&!(flags&((1ull<<2)|(1ull<<9)|(1ull<<10)|(1ull<<13)|(1ull<<15)|(1ull<<18)|(1ull<<22)));
 }
 bool read(EffectValue& value) const noexcept {
  if(!check())return false;
  value={};
  if(kEffects[id].type==0){unsigned char v=0;if(!read_value(ref.data+0x58,v)||v>1)return false;value[0]=v;}
  else if(kEffects[id].type==3){std::int32_t v=0;if(!read_value(ref.data+0x58,v))return false;value[0]=v;}
  else {
   std::array<float,4> v{};
   if(!read_memory(ref.data+0x58,v.data(),kEffects[id].components*sizeof(float)))return false;
   for(unsigned i=0;i<kEffects[id].components;++i)value[i]=v[i];
  }
  for(unsigned i=0;i<kEffects[id].components;++i)if(!std::isfinite(value[i]))return false;
  return true;
 }
 bool read(double& value) const noexcept {
  EffectValue result{};if(kEffects[id].components!=1||!read(result))return false;value=result[0];return true;
 }
 bool write(const EffectValue& value) noexcept {
  EffectValue previous{};if(!gSetCvar||!read(previous))return false;
  alignas(16) unsigned char typed[16]{};EffectValue expected{};
  for(unsigned i=0;i<kEffects[id].components;++i){
   if(!std::isfinite(value[i]))return false;
   if(kEffects[id].type==0){if(value[i]!=0&&value[i]!=1)return false;typed[0]=static_cast<unsigned char>(value[i]);expected[i]=value[i];}
   else if(kEffects[id].type==3){if(value[i]!=std::floor(value[i])||value[i]<INT32_MIN||value[i]>INT32_MAX)return false;auto v=static_cast<std::int32_t>(value[i]);std::memcpy(typed,&v,4);expected[i]=v;}
   else {float v=static_cast<float>(value[i]);if(!std::isfinite(v))return false;std::memcpy(typed+4*i,&v,4);expected[i]=v;}
  }
  if(previous==expected)return true;
  // Preserves native filter/change callbacks, clamping and change count.
  // A vector update is one typed setter call, never four console deliveries.
  gSetCvar(&ref,0,typed,reinterpret_cast<void*>(ref.data+0x58),nullptr);
  EffectValue actual{};return read(actual)&&actual==expected;
 }
};
struct NativeEffectState {
 std::shared_ptr<const NativeShot> shot;
 std::array<EffectBinding,kEffects.size()> bindings{};
 unsigned count=0,error=0;std::uint64_t frames=0;double phase=0;
 bool restore() noexcept {
  bool okay=true;
  for(unsigned i=0;i<count;++i)if(!bindings[i].write(bindings[i].restore))okay=false;
  if(okay){count=0;shot.reset();error=0;}else error=3;
  return okay;
 }
 bool apply(const std::shared_ptr<const NativeShot>& next,double at) noexcept {
  if(next!=shot){
   if(!restore())return false;
   if(!next||next->effects.empty())return true;
   if(!gCvar||!gFindCvar||!gGetCvarData||!gSetCvar){error=1;return false;}
   std::array<EffectBinding,kEffects.size()> candidate{};unsigned candidate_count=0;
   // Resolve and read every full original before mutating any setting.
   for(const auto& track:next->effects){
    unsigned index=0;for(;index<candidate_count&&candidate[index].id!=track.id;++index){}
    if(index==candidate_count){
     if(candidate_count>=candidate.size()){error=1;return false;}
     auto& b=candidate[candidate_count++];b.id=track.id;
     gFindCvar(reinterpret_cast<void*>(gCvar),&b.ref.id,kEffects[b.id].name,0);
     if((b.ref.id&0xffff)==0xffff){error=1;return false;}
     b.ref.data=gGetCvarData(reinterpret_cast<void*>(gCvar),b.ref.id);
     if(!b.read(b.restore)){error=1;return false;}
    }
    if(track.restore_override)candidate[index].restore[track.component]=track.restore;
   }
   bindings=candidate;count=candidate_count;shot=next;frames=0;
  }
  if(!count)return true;
  std::array<EffectValue,kEffects.size()> values{};
  for(const auto& track:shot->effects){
   unsigned index=0;for(;index<count&&bindings[index].id!=track.id;++index){}
   if(index==count||!track.evaluate(at,values[index][track.component])){error=2;return false;}
  }
  for(unsigned i=0;i<count;++i)if(!bindings[i].write(values[i])){error=2;return false;}
  phase=at;++frames;error=0;return true;
 }
};
NativeEffectState gEffects;
static bool init_cvar_interface() {
 auto tier0=GetModuleHandleW(L"tier0.dll");
 if(!module_matches(tier0,kTier0Hash,0x400000)&&!module_matches(tier0,kUpdatedTier0Hash,0x400000))return false;
 gExtendedCvarSupported=module_matches(tier0,kUpdatedTier0Hash,0x400000);
 gTier0=reinterpret_cast<std::uintptr_t>(tier0);
 auto factory=reinterpret_cast<Factory>(GetProcAddress(tier0,"CreateInterface"));
 if(!factory)return false;
 gCvar=reinterpret_cast<std::uintptr_t>(factory("VEngineCvar007",nullptr));
 std::uintptr_t table=0,find=0,data=0;
 if(!read_value(gCvar,table)||table!=gTier0+kCvarTable||!read_value(table+11*8,find)||find!=gTier0+0x6bef0||!read_value(table+43*8,data)||data!=gTier0+0x6be30)return false;
 const unsigned char expected[]={0x4c,0x89,0x44,0x24,0x18,0x48,0x89,0x4c,0x24,0x08,0x55,0x56,0x57,0x41,0x54,0x41};
 unsigned char actual[sizeof(expected)]{};
 if(!read_memory(gTier0+kCvarSet,actual,sizeof(actual))||std::memcmp(actual,expected,sizeof(actual)))return false;
 gFindCvar=reinterpret_cast<FindCvarFn>(find);gGetCvarData=reinterpret_cast<CvarDataFn>(data);gSetCvar=reinterpret_cast<SetCvarFn>(gTier0+kCvarSet);return true;
}
