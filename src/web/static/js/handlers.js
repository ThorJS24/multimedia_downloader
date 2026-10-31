// Event handler registry. Markup refers to these by key: data-on-<event>="key" (+ data-args-<event>='[...]').
// Called with `this` = the element and (event, args). Add new handlers here; never use inline on*= attributes
// (the Content-Security-Policy forbids them).
(function () {
"use strict";
var H = (window.UI_HANDLERS = window.UI_HANDLERS || {});

H["addMonitoredArtist_82757a"] = function (event, __a) {
    addMonitoredArtist();
};

H["addNewWatchedFolder_17164a"] = function (event, __a) {
    addNewWatchedFolder();
};

H["applyApprovedMetadataRepair_c01f60"] = function (event, __a) {
    applyApprovedMetadataRepair();
};

H["applyBatchEditModal_64f95f"] = function (event, __a) {
    applyBatchEditModal();
};

H["applyBatchTags_fdfbd4"] = function (event, __a) {
    applyBatchTags();
};

H["applyLogFilters_c08f38"] = function (event, __a) {
    applyLogFilters();
};

H["approveAllWatcherPending_ecca0b"] = function (event, __a) {
    approveAllWatcherPending();
};

H["approveWatcherItem_bb7542"] = function (event, __a) {
    approveWatcherItem('' + __a[0] + '');
};

H["batchExportM3U_da5815"] = function (event, __a) {
    batchExportM3U();
};

H["batchQueueAction_22e738"] = function (event, __a) {
    batchQueueAction('remove');
};

H["batchQueueAction_527922"] = function (event, __a) {
    batchQueueAction('move_top');
};

H["batchQueueAction_7da659"] = function (event, __a) {
    batchQueueAction('move_bottom');
};

H["batchQueueAction_a06714"] = function (event, __a) {
    batchQueueAction('retry');
};

H["batchQueueAction_a8c119"] = function (event, __a) {
    batchQueueAction('cancel');
};

H["batchReindex_89e89c"] = function (event, __a) {
    batchReindex();
};

H["cancelLibraryScan_e349d8"] = function (event, __a) {
    cancelLibraryScan();
};

H["cancelQueue_703ee0"] = function (event, __a) {
    cancelQueue();
};

H["changeLibraryLimit_b43bc3"] = function (event, __a) {
    changeLibraryLimit(this.value);
};

H["changeLibraryPage_40a28d"] = function (event, __a) {
    changeLibraryPage(-1);
};

H["changeLibraryPage_923875"] = function (event, __a) {
    changeLibraryPage(1);
};

H["changeQueuePage_5ad998"] = function (event, __a) {
    changeQueuePage(currentQueuePage + 1);
};

H["changeQueuePage_8e41e5"] = function (event, __a) {
    changeQueuePage(currentQueuePage - 1);
};

H["changeQueuePage_99f53a"] = function (event, __a) {
    changeQueuePage(totalQueuePages);
};

H["changeQueuePage_a9fd29"] = function (event, __a) {
    changeQueuePage(1);
};

H["checkAllMonitorsNow_83ebea"] = function (event, __a) {
    checkAllMonitorsNow();
};

H["cleanTempBuffers_13ef97"] = function (event, __a) {
    cleanTempBuffers();
};

H["clearCurrentAlternative_e89960"] = function (event, __a) {
    clearCurrentAlternative();
};

H["clearFailedQueue_469b8a"] = function (event, __a) {
    clearFailedQueue();
};

H["clearFetchInput_14b209"] = function (event, __a) {
    clearFetchInput();
};

H["clearFinishedQueue_29a19b"] = function (event, __a) {
    clearFinishedQueue();
};

H["clearGlobalSearch_82138c"] = function (event, __a) {
    clearGlobalSearch();
};

H["clearLibrarySelection_a47a73"] = function (event, __a) {
    clearLibrarySelection();
};

H["clearLogs_37ead7"] = function (event, __a) {
    clearLogs();
};

H["clearPlayerQueue_d67ecf"] = function (event, __a) {
    clearPlayerQueue();
};

H["clearQueueSearch_728c9d"] = function (event, __a) {
    clearQueueSearch();
};

H["clearUnifiedSearch_03035f"] = function (event, __a) {
    clearUnifiedSearch();
};

H["closeAlternativesModal_4bca9d"] = function (event, __a) {
    closeAlternativesModal();
};

H["closeBatchEditModal_8f5a51"] = function (event, __a) {
    closeBatchEditModal();
};

H["closeBatchTagModal_33b8fa"] = function (event, __a) {
    closeBatchTagModal();
};

H["closeBulkOrganizerModal_9815d1"] = function (event, __a) {
    closeBulkOrganizerModal();
};

H["closeEditQueueJobModal_9d2192"] = function (event, __a) {
    closeEditQueueJobModal();
};

H["closeInspectorModal_eee9a7"] = function (event, __a) {
    closeInspectorModal();
};

H["closeLoginModal_a26dbc"] = function (event, __a) {
    closeLoginModal();
};

H["closeMetadataRepairModal_3a9021"] = function (event, __a) {
    closeMetadataRepairModal();
};

H["closeQueueExportMenu_6dcc6e"] = function (event, __a) {
    closeQueueExportMenu();
};

H["closeQueueJobDetailsModal_9ea545"] = function (event, __a) {
    closeQueueJobDetailsModal();
};

H["closeStagingModal_f85b44"] = function (event, __a) {
    closeStagingModal();
};

H["closeTagModal_268d7c"] = function (event, __a) {
    closeTagModal();
};

H["closeWizardModal_7ceab6"] = function (event, __a) {
    closeWizardModal();
};

H["closeYouTubePlayerModal_4e5bff"] = function (event, __a) {
    closeYouTubePlayerModal();
};

H["commitStagingBatch_aa91e7"] = function (event, __a) {
    commitStagingBatch();
};

H["completeSetupWizard_b4a5d3"] = function (event, __a) {
    completeSetupWizard();
};

H["copyQueueJobError_815c0d"] = function (event, __a) {
    copyQueueJobError();
};

H["debounceLibSearch_20f7ac"] = function (event, __a) {
    debounceLibSearch();
};

H["deleteDuplicateSafe_7cfde6"] = function (event, __a) {
    deleteDuplicateSafe('' + __a[0] + '');
};

H["deleteMonitoredArtist_59cdd4"] = function (event, __a) {
    deleteMonitoredArtist('' + __a[0] + '');
};

H["deselectAllQueueItems_ac8c98"] = function (event, __a) {
    deselectAllQueueItems();
};

H["dismissWatcherItem_2f3b9a"] = function (event, __a) {
    dismissWatcherItem('' + __a[0] + '');
};

H["document_1ddd41"] = function (event, __a) {
    document.getElementById('audio-element').playbackRate = parseFloat(this.value);
};

H["document_2df6de"] = function (event, __a) {
    document.getElementById('cfg-workers-label').innerText = this.value + ' Worker' + (this.value > 1 ? 's' : ' (Safe)');
};

H["drillDownToDuplicates_193278"] = function (event, __a) {
    drillDownToDuplicates('exact');
};

H["drillDownToDuplicates_f512c3"] = function (event, __a) {
    drillDownToDuplicates('probable');
};

H["drillDownToLibrary_14ca1f"] = function (event, __a) {
    drillDownToLibrary('unreadable');
};

H["drillDownToLibrary_3064c5"] = function (event, __a) {
    drillDownToLibrary('anomalies');
};

H["drillDownToLibrary_4f27fa"] = function (event, __a) {
    drillDownToLibrary('missing_tags');
};

H["drillDownToLibrary_c0fb70"] = function (event, __a) {
    drillDownToLibrary('missing_art');
};

H["enqueueUnifiedItem_19995d"] = function (event, __a) {
    enqueueUnifiedItem(__a[0]);
};

H["event_d74500"] = function (event, __a) {
    event.stopPropagation();
};

H["executeBulkOrganizer_314c24"] = function (event, __a) {
    executeBulkOrganizer();
};

H["exportAuditCSV_c13910"] = function (event, __a) {
    exportAuditCSV();
};

H["exportCurrentAlbumM3U_44b901"] = function (event, __a) {
    exportCurrentAlbumM3U();
};

H["exportCurrentPlaylistM3U_dffa54"] = function (event, __a) {
    exportCurrentPlaylistM3U();
};

H["exportDiagnostics_97813a"] = function (event, __a) {
    exportDiagnostics();
};

H["exportM3U_e02fcf"] = function (event, __a) {
    exportM3U();
};

H["exportSelectedQueue_0c98fa"] = function (event, __a) {
    exportSelectedQueue('csv');
};

H["fetchLinkDirectly_bde04b"] = function (event, __a) {
    fetchLinkDirectly('' + __a[0] + '');
};

H["fetchRepairQueue_6d77f4"] = function (event, __a) {
    fetchRepairQueue();
};

H["fetchWatcherStatus_401672"] = function (event, __a) {
    fetchWatcherStatus();
};

H["filterStagingTable_611a0c"] = function (event, __a) {
    filterStagingTable('ready');
};

H["filterStagingTable_6de5e5"] = function (event, __a) {
    filterStagingTable('archived');
};

H["filterStagingTable_78316d"] = function (event, __a) {
    filterStagingTable('all');
};

H["filterStagingTable_c3d3bb"] = function (event, __a) {
    filterStagingTable('collision');
};

H["handleFileImport_f3d995"] = function (event, __a) {
    handleFileImport(event);
};

H["handleGlobalSearch_d4656e"] = function (event, __a) {
    handleGlobalSearch(this.value);
};

H["handleQueueCheckboxClick_e6f254"] = function (event, __a) {
    handleQueueCheckboxClick('' + __a[0] + '', event);
};

H["handleQueueFilterChange_9d597f"] = function (event, __a) {
    handleQueueFilterChange();
};

H["handleQueuePageSizeChange_6e7bb9"] = function (event, __a) {
    handleQueuePageSizeChange();
};

H["handleUnifiedSearchInput_3e0fdd"] = function (event, __a) {
    handleUnifiedSearchInput(this.value);
};

H["loadExactDuplicates_8ea43f"] = function (event, __a) {
    loadExactDuplicates();
};

H["loadProbableDuplicates_5f5318"] = function (event, __a) {
    loadProbableDuplicates();
};

H["moveDuplicateToBackup_114185"] = function (event, __a) {
    moveDuplicateToBackup('' + __a[0] + '');
};

H["navHistoryBack_1149f5"] = function (event, __a) {
    navHistoryBack();
};

H["navHistoryForward_21aab3"] = function (event, __a) {
    navHistoryForward();
};

H["openAlbumView_7a0637"] = function (event, __a) {
    event.stopPropagation(); openAlbumView('' + __a[0] + '', '' + __a[1] + '');
};

H["openAlbumView_810177"] = function (event, __a) {
    openAlbumView('' + __a[0] + '', '' + __a[1] + '');
};

H["openAlternativesModal_8254e2"] = function (event, __a) {
    openAlternativesModal(__a[0]);
};

H["openArtistFromCurrentAlbum_9de5f1"] = function (event, __a) {
    openArtistFromCurrentAlbum();
};

H["openArtistView_302f1a"] = function (event, __a) {
    event.stopPropagation(); openArtistView('' + __a[0] + '');
};

H["openArtistView_53ee7d"] = function (event, __a) {
    openArtistView('' + __a[0] + '');
};

H["openBatchEditModal_9a0f62"] = function (event, __a) {
    openBatchEditModal();
};

H["openBatchTagModal_775723"] = function (event, __a) {
    openBatchTagModal();
};

H["openBulkOrganizerModal_03801e"] = function (event, __a) {
    openBulkOrganizerModal();
};

H["openDownloadsFolder_295db9"] = function (event, __a) {
    openDownloadsFolder();
};

H["openEditModalFromDetails_3dccc0"] = function (event, __a) {
    openEditModalFromDetails();
};

H["openEditQueueJobModal_6f708d"] = function (event, __a) {
    openEditQueueJobModal('' + __a[0] + '');
};

H["openFileInspector_45a42b"] = function (event, __a) {
    openFileInspector('' + __a[0] + '');
};

H["openLoginModal_b5b7e1"] = function (event, __a) {
    openLoginModal();
};

H["openMetadataRepairModal_6e1598"] = function (event, __a) {
    openMetadataRepairModal('' + __a[0] + '');
};

H["openPlaylistView_0c783b"] = function (event, __a) {
    openPlaylistView('' + __a[0] + '');
};

H["openQueueJobDetailsModal_507735"] = function (event, __a) {
    openQueueJobDetailsModal('' + __a[0] + '');
};

H["openTagModalFromInspector_d05512"] = function (event, __a) {
    openTagModalFromInspector();
};

H["openTagModal_38155e"] = function (event, __a) {
    openTagModal('' + __a[0] + '', '' + __a[1] + '', '' + __a[2] + '', '' + __a[3] + '');
};

H["openWizardModal_852407"] = function (event, __a) {
    openWizardModal();
};

H["openYouTubePlayerModal_bfbb03"] = function (event, __a) {
    openYouTubePlayerModal(__a[0]);
};

H["pasteClipboard_a2cb50"] = function (event, __a) {
    pasteClipboard();
};

H["pauseResumeLibraryScan_3dd069"] = function (event, __a) {
    pauseResumeLibraryScan();
};

H["playCurrentAlbum_b2b833"] = function (event, __a) {
    playCurrentAlbum();
};

H["playCurrentArtist_b43d60"] = function (event, __a) {
    playCurrentArtist();
};

H["playCurrentPlaylist_fd9079"] = function (event, __a) {
    playCurrentPlaylist();
};

H["playLocalTrack_15214c"] = function (event, __a) {
    playLocalTrack('' + __a[0] + '', '' + __a[1] + '', '' + __a[2] + '', true, '' + __a[3] + '');
};

H["playLocalTrack_9bca49"] = function (event, __a) {
    playLocalTrack('' + __a[0] + '', '' + __a[1] + '', '' + __a[2] + '', false, '' + __a[3] + '');
};

H["playNextTrack_f68b2b"] = function (event, __a) {
    playNextTrack();
};

H["playPrevTrack_7c9ea5"] = function (event, __a) {
    playPrevTrack();
};

H["previewBulkOrganizer_cb379d"] = function (event, __a) {
    previewBulkOrganizer();
};

H["queueCurrentAlbum_316bba"] = function (event, __a) {
    queueCurrentAlbum();
};

H["queueCurrentArtist_058352"] = function (event, __a) {
    queueCurrentArtist();
};

H["queueCurrentPlaylist_9a4c99"] = function (event, __a) {
    queueCurrentPlaylist();
};

H["queueLocalTrack_8df91f"] = function (event, __a) {
    event.stopPropagation(); queueLocalTrack('' + __a[0] + '', '' + __a[1] + '', '' + __a[2] + '');
};

H["removeJob_0cb4f8"] = function (event, __a) {
    removeJob('' + __a[0] + '');
};

H["removeTrackFromQueue_41fcc9"] = function (event, __a) {
    event.stopPropagation(); removeTrackFromQueue(__a[0]);
};

H["removeWatchedFolderPrompt_f4854f"] = function (event, __a) {
    removeWatchedFolderPrompt('' + __a[0] + '', '' + __a[1] + '');
};

H["reorderJob_0de448"] = function (event, __a) {
    reorderJob('' + __a[0] + '', 'down');
};

H["reorderJob_f1c63a"] = function (event, __a) {
    reorderJob('' + __a[0] + '', 'up');
};

H["requestStagingPreview_2a6683"] = function (event, __a) {
    requestStagingPreview();
};

H["rescanAllWatchedFolders_0176df"] = function (event, __a) {
    rescanAllWatchedFolders();
};

H["rescanWatchedFolder_009c0a"] = function (event, __a) {
    rescanWatchedFolder('' + __a[0] + '');
};

H["resetAllQueueFilters_33fc4d"] = function (event, __a) {
    resetAllQueueFilters();
};

H["resetSettingsDefaults_69a04d"] = function (event, __a) {
    resetSettingsDefaults();
};

H["resolveSubmittedLinks_132dec"] = function (event, __a) {
    resolveSubmittedLinks();
};

H["retryAllFailedJobs_b485ca"] = function (event, __a) {
    retryAllFailedJobs();
};

H["retryJobFromDetails_452c9b"] = function (event, __a) {
    retryJobFromDetails();
};

H["retryJob_06628e"] = function (event, __a) {
    retryJob('' + __a[0] + '');
};

H["rollbackBulkOrganizer_ab3d2c"] = function (event, __a) {
    rollbackBulkOrganizer();
};

H["runLibraryQualityAudit_b859c0"] = function (event, __a) {
    runLibraryQualityAudit();
};

H["runReleaseComparison_e9f1f2"] = function (event, __a) {
    runReleaseComparison();
};

H["runSyncApply_f9ecb2"] = function (event, __a) {
    runSyncApply();
};

H["runSyncCompare_a6999e"] = function (event, __a) {
    runSyncCompare(true);
};

H["saveAllSettings_6c36d5"] = function (event, __a) {
    saveAllSettings();
};

H["saveEditQueueJob_ce995a"] = function (event, __a) {
    saveEditQueueJob();
};

H["saveEditedTags_0d2cae"] = function (event, __a) {
    saveEditedTags();
};

H["scanAcousticDuplicates_954d9c"] = function (event, __a) {
    scanAcousticDuplicates();
};

H["seekAudio_ba9065"] = function (event, __a) {
    seekAudio(this.value);
};

H["selectAlternativeCandidate_09f876"] = function (event, __a) {
    selectAlternativeCandidate(__a[0]);
};

H["selectQueuePreset_14ca95"] = function (event, __a) {
    selectQueuePreset('all_filtered');
};

H["selectQueuePreset_196cd7"] = function (event, __a) {
    selectQueuePreset('queued');
};

H["selectQueuePreset_58450d"] = function (event, __a) {
    selectQueuePreset('invert');
};

H["selectQueuePreset_9c5607"] = function (event, __a) {
    selectQueuePreset('failed');
};

H["selectQueuePreset_a8d89d"] = function (event, __a) {
    selectQueuePreset('page');
};

H["setAudioVolume_55ac7a"] = function (event, __a) {
    setAudioVolume(this.value);
};

H["setLibraryFilter_1388dd"] = function (event, __a) {
    setLibraryFilter('anomalies');
};

H["setLibraryFilter_4242e0"] = function (event, __a) {
    setLibraryFilter('missing_art');
};

H["setLibraryFilter_4db58b"] = function (event, __a) {
    setLibraryFilter('review');
};

H["setLibraryFilter_5b4684"] = function (event, __a) {
    setLibraryFilter('missing_tags');
};

H["setLibraryFilter_6c3d65"] = function (event, __a) {
    setLibraryFilter('exact_duplicates');
};

H["setLibraryFilter_dfb94f"] = function (event, __a) {
    setLibraryFilter('all');
};

H["setLogSeverityFilter_02b2dc"] = function (event, __a) {
    setLogSeverityFilter('ERROR');
};

H["setLogSeverityFilter_23cc65"] = function (event, __a) {
    setLogSeverityFilter('ALL');
};

H["setLogSeverityFilter_2a63c9"] = function (event, __a) {
    setLogSeverityFilter('WARNING');
};

H["setLogSeverityFilter_c3c05b"] = function (event, __a) {
    setLogSeverityFilter('INFO');
};

H["setOmniCategory_4ae843"] = function (event, __a) {
    setOmniCategory('tracks');
};

H["setOmniCategory_9b7c32"] = function (event, __a) {
    setOmniCategory('videos');
};

H["setOmniCategory_a421af"] = function (event, __a) {
    setOmniCategory('albums');
};

H["setOmniCategory_b94fb1"] = function (event, __a) {
    setOmniCategory('all');
};

H["setOmniCategory_b9d245"] = function (event, __a) {
    setOmniCategory('artists');
};

H["setOmniCategory_ec9585"] = function (event, __a) {
    setOmniCategory('playlists');
};

H["setQueueStatusFilter_328923"] = function (event, __a) {
    setQueueStatusFilter('ALL');
};

H["setQueueStatusFilter_89a6b8"] = function (event, __a) {
    setQueueStatusFilter('COMPLETED');
};

H["setQueueStatusFilter_96cc81"] = function (event, __a) {
    setQueueStatusFilter('QUEUED');
};

H["setQueueStatusFilter_996ffc"] = function (event, __a) {
    setQueueStatusFilter('FAILED');
};

H["setQueueStatusFilter_9fb0ca"] = function (event, __a) {
    setQueueStatusFilter('ACTIVE');
};

H["setUnifiedScope_1b92d1"] = function (event, __a) {
    setUnifiedScope('youtube');
};

H["setUnifiedScope_385a4d"] = function (event, __a) {
    setUnifiedScope('ytmusic');
};

H["setUnifiedScope_94febe"] = function (event, __a) {
    setUnifiedScope('all');
};

H["setUnifiedScope_bdbc6d"] = function (event, __a) {
    setUnifiedScope('local');
};

H["setUnifiedScope_decf47"] = function (event, __a) {
    setUnifiedScope('spotify');
};

H["stagingSelectAll_3dd643"] = function (event, __a) {
    stagingSelectAll(true);
};

H["stagingSelectAll_a0f435"] = function (event, __a) {
    stagingSelectAll(false);
};

H["stagingToggleMaster_8b2b7b"] = function (event, __a) {
    stagingToggleMaster(this.checked);
};

H["startLibraryBgScan_f08b35"] = function (event, __a) {
    startLibraryBgScan();
};

H["startWatcher_c6c9ff"] = function (event, __a) {
    startWatcher();
};

H["stopWatcher_e3b0c0"] = function (event, __a) {
    stopWatcher();
};

H["switchDuplicateTab_70783c"] = function (event, __a) {
    switchDuplicateTab('probable');
};

H["switchDuplicateTab_f96232"] = function (event, __a) {
    switchDuplicateTab('exact');
};

H["switchTab_177d2b"] = function (event, __a) {
    switchTab('search');
};

H["switchTab_42a907"] = function (event, __a) {
    switchTab('queue'); closeStagingModal();
};

H["switchTab_c57614"] = function (event, __a) {
    switchTab('library');
};

H["testSpotifyCredentials_a727e6"] = function (event, __a) {
    testSpotifyCredentials();
};

H["this_49d186"] = function (event, __a) {
    this.classList.add('hidden'); this.nextElementSibling.classList.remove('hidden');
};

H["this_4b0b04"] = function (event, __a) {
    this.src=''; this.className='hidden';
};

H["this_8b6d44"] = function (event, __a) {
    this.classList.add('hidden'); document.getElementById('artist-view-placeholder').classList.remove('hidden');
};

H["this_9ed8ca"] = function (event, __a) {
    this.classList.add('hidden'); document.getElementById('player-placeholder-icon').classList.remove('hidden');
};

H["this_b43eda"] = function (event, __a) {
    this.classList.add('hidden'); document.getElementById('album-view-placeholder').classList.remove('hidden');
};

H["this_c25d64"] = function (event, __a) {
    this.src='';
};

H["this_c9a455"] = function (event, __a) {
    this.classList.add('hidden'); document.getElementById('drawer-placeholder-icon').classList.remove('hidden');
};

H["this_d32728"] = function (event, __a) {
    this.classList.add('hidden'); document.getElementById('playlist-view-placeholder').classList.remove('hidden');
};

H["this_f2cb27"] = function (event, __a) {
    this.style.display='none';
};

H["toggleAudioMute_038e99"] = function (event, __a) {
    toggleAudioMute();
};

H["toggleAudioPlayback_0bf895"] = function (event, __a) {
    toggleAudioPlayback();
};

H["toggleLibraryRowSelection_ff200f"] = function (event, __a) {
    toggleLibraryRowSelection(this);
};

H["toggleMasterSelectMenu_1f138c"] = function (event, __a) {
    toggleMasterSelectMenu(event);
};

H["toggleNowPlayingDrawer_cfe424"] = function (event, __a) {
    toggleNowPlayingDrawer();
};

H["togglePauseQueue_ff69d3"] = function (event, __a) {
    togglePauseQueue();
};

H["togglePauseWatcher_c6135c"] = function (event, __a) {
    togglePauseWatcher();
};

H["togglePlaybackQueueDrawer_ababec"] = function (event, __a) {
    togglePlaybackQueueDrawer();
};

H["toggleQueueExportMenu_381587"] = function (event, __a) {
    toggleQueueExportMenu();
};

H["toggleRepeat_bf196a"] = function (event, __a) {
    toggleRepeat();
};

H["toggleSecretVisibility_1c5a48"] = function (event, __a) {
    toggleSecretVisibility();
};

H["toggleSelectAllCurrentPage_3d5f46"] = function (event, __a) {
    toggleSelectAllCurrentPage(this.checked);
};

H["toggleSelectAllLibrary_3fb3ae"] = function (event, __a) {
    toggleSelectAllLibrary(this.checked);
};

H["toggleSelectAllQueueFiltered_84dc8b"] = function (event, __a) {
    toggleSelectAllQueueFiltered(true);
};

H["toggleShuffle_7c6faf"] = function (event, __a) {
    toggleShuffle();
};

H["toggleSortColumn_13480a"] = function (event, __a) {
    toggleSortColumn('progress');
};

H["toggleSortColumn_273a26"] = function (event, __a) {
    toggleSortColumn('title');
};

H["toggleSortColumn_40c048"] = function (event, __a) {
    toggleSortColumn('status');
};

H["toggleSortColumn_57decb"] = function (event, __a) {
    toggleSortColumn('album');
};

H["toggleSortColumn_5b1097"] = function (event, __a) {
    toggleSortColumn('default');
};

H["toggleStagingItem_76a8bb"] = function (event, __a) {
    toggleStagingItem('' + __a[0] + '', this.checked);
};

H["toggleWatchedFolderEnabled_221f1b"] = function (event, __a) {
    toggleWatchedFolderEnabled('' + __a[0] + '', __a[1]);
};

H["triggerFileImport_9d64a3"] = function (event, __a) {
    triggerFileImport();
};

H["checkForUpdates_a1"] = function (event, __a) {
    checkForUpdates();
};

H["downloadUpdate_a1"] = function (event, __a) {
    downloadUpdate();
};

H["installUpdate_a1"] = function (event, __a) {
    installUpdate();
};
H["downloadAlternativeCandidate_a1"] = function (event, __a) {
    downloadAlternativeCandidate(__a[0]);
};

H["openHubFromNav"] = function (event, __a) {
    openHub(__a[0], true);
};

H["goTab"] = function (event, __a) {
    switchTab(__a[0], true, false);
};

H["toggleSidebar"] = function (event, __a) {
    toggleSidebar();
};

H["cycleTheme"] = function (event, __a) {
    cycleTheme();
};

H["setThemeChoice"] = function (event, __a) {
    setThemeChoice(this.value);
};

H["openWatcherTab"] = function (event, __a) {
    switchTab('watcher');
};

H["globalSearchKey"] = function (event, __a) {
    globalSearchKey(event, this);
};

H["homeQuickAdd"] = function (event, __a) {
    homeQuickAdd(event);
};

H["updateLinkHint"] = function (event, __a) {
    updateLinkHint();
};

H["signOut"] = function (event, __a) {
    signOutAccount();
};

H["toggleLoginPassword"] = function (event, __a) {
    toggleLoginPassword(this);
};

H["jumpToSetting"] = function (event, __a) {
    jumpToSetting(event, this);
};

H["discardSettings"] = function (event, __a) {
    discardSettings();
};

H["browseDownloadsDir"] = function (event, __a) {
    browseForFolder('cfg-downloads-dir');
};

H["browseWatchFolder"] = function (event, __a) {
    browseForFolder('new-watch-path');
};

H["applyDownloadsDir"] = function (event, __a) {
    applyDownloadsDir();
};

H["createBackupNow"] = function (event, __a) {
    createBackupNow();
};

H["pickSettingsImport"] = function (event, __a) {
    pickSettingsImport();
};

H["importSettingsFile"] = function (event, __a) {
    importSettingsFile(event);
};

H["sortLibraryColumn"] = function (event, __a) {
    setLibrarySort(__a[0]);
};

H["refreshLibrary"] = function (event, __a) {
    refreshLibrary();
};

H["clearLibraryFilters"] = function (event, __a) {
    clearLibraryFilters();
};

H["submitLoginForm"] = function (event, __a) {
    submitLogin(event);
};

H["submitMatchForm"] = function (event, __a) {
    submitMatchForm(event);
};

H["toggleColumnMenu"] = function (event, __a) {
    toggleColumnMenu();
};

H["toggleLibraryColumn"] = function (event, __a) {
    toggleLibraryColumn(__a[0], this.checked);
};

H["resetLibraryColumns"] = function (event, __a) {
    resetLibraryColumns();
};

H["applyLibraryViewFromSelect"] = function (event, __a) {
    applyLibraryView(this.value);
};

H["saveLibraryView"] = function (event, __a) {
    saveLibraryView();
};

H["deleteLibraryView"] = function (event, __a) {
    deleteLibraryView();
};

H["setFxChoiceFromInput"] = function (event, __a) {
    setFxChoice(this.value);
};

H["wizardNext"] = function (event, __a) {
    wizardNext();
};

H["wizardBack"] = function (event, __a) {
    wizardBack();
};

H["browseWizardFolder"] = function (event, __a) {
    browseForFolder('wizard-downloads-path');
};

})();
