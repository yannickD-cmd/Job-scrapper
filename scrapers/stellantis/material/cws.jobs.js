var CWS = CWS || {};
$(document).ready(function() {
    $("#cws-adv-search-btn, #clear-all-btn").on('keydown', function (e) {
        var ele = document.activeElement.id;
        if(e.keyCode == 32){
            event.preventDefault();
            if(ele === "cws-adv-search-btn"){
                $('#cws-adv-search-btn').click();
            }
            else if(ele === "clear-all-btn"){
                $('#clear-all-btn').click();
            }
        }
    });
    if($('.widget_archive').length > 0){
        $('.widget_archive li').each(function(){
            var blogName = $(this).find('a').text();
            $(this).find('a').attr('aria-label','Archive blog for '+blogName);
        });
    }
    //Expand/Collapse feature for search filter checkboxes will work only when it is enabled under sitewide settings.
    if(cws_opts.sitewide.search_filter_collapsible_checkboxes == "1") {
        searchFiltersCollapsibleCheckboxes();
    }
	
    if($("#cws-search-form").css('display') == 'block'){
        $("#toggleAdvSearch").attr('aria-expanded','true');
    }else{
        $("#toggleAdvSearch").attr('aria-expanded','false');  
        $("#toggleAdvSearch").addClass('close'); 
    }
    
    $('.widget-jobsearch-results.table_tile.list').find('.search-results-table').attr("role","table");
    // To handle checkbox values in cws job quick search v2 widget
    // Commented with introduction of chaining search criteria option
    // [] would be added on form submit only
    // multi_checkbox('cws_quickjobsearch');
	
	// Job search filter - comboboxes accessibility
	combobox_accessibility();
	select2_navigation();

    // Job search filter - Keyword field accessibility
    $('.keyword_suggest').on('input', function(){
        var inputValue = $(this).val();
        setTimeout(function () {
            if(inputValue.length > 1 && $(".ui-autocomplete").css('display') == 'block'){
                $('.keyword_suggest').attr('aria-expanded','true');
                $('.ui-autocomplete li').attr('role','option').removeAttr('tabindex');
                $('.ui-autocomplete').attr('role', 'listbox').removeAttr('tabindex');
                $('.ui-autocomplete li').each(function(){
                    var $aTag = $(this).find('a');
                    $(this).attr('id', $aTag.attr('id'));
                    $aTag.replaceWith(function() {
                        return $('<span>', {
                            html: $(this).html(),
                            class: $(this).attr('class')
                        });
                    });
                });
            }
            else{
                $('.keyword_suggest').attr('aria-expanded','false');
            }
        }, 2000);
    });
    /* Keyword auto suggestion - accessibilty for suggested list items */
    let isArrowKey = false;
    let lastFocusedItem = null;
    $(".keyword_suggest").on("keydown", function(e) {
        const keyword_input = $(this);
        if (e.which === 38 || e.which === 40) {
            isArrowKey = true;
            if($('.ui-autocomplete li:first-child')){
                $('.ui-helper-hidden-accessible').attr('aria-live', 'off');
            }
            setTimeout(function() {
                $('.ui-helper-hidden-accessible').attr('aria-live', 'off');
                const activeItem = $('.ui-autocomplete li .ui-state-active');
                if (activeItem.length) {
                    keyword_input.attr('aria-activedescendant', activeItem.parent().attr('id'));
                }
            }, 10);
        } else {
            $('.ui-helper-hidden-accessible').attr('aria-live', 'assertive');
            isArrowKey = false;
        }
        if (e.which === 27) { 
            if (lastFocusedItem) {
                keyword_input.val(lastFocusedItem.label); 
            }
            $(this).autocomplete("close");
        }
    });
    $(".keyword_suggest").autocomplete({
        focus: function(event, ui) {
            if (!isArrowKey) {
                return false;
            }
            const keyword_input = $(this);
            lastFocusedItem = ui.item;
            $('.ui-autocomplete li').removeAttr('aria-selected').removeAttr('tabindex');
            $('.ui-autocomplete li').each(function() {
                if ($(this).text().trim() === ui.item.label.trim()) {
                    $(this).attr('aria-selected', 'true').removeAttr('tabindex');
                    const keyword_id = $(this).attr('id');
                    if (keyword_id) {
                        keyword_input.attr('aria-activedescendant', keyword_id);
                    }
                }
            });
            keyword_input.val(ui.item.label);
            return false;
        },
        open: function() {
            $('.ui-helper-hidden-accessible').attr('aria-live', 'assertive');
        }
    });
    $(".keyword_suggest").on("autocompleteclose", function() {
        $(this).removeAttr('aria-activedescendant');
        $('.ui-autocomplete li').removeAttr('aria-selected').removeAttr('tabindex');
        $('.ui-helper-hidden-accessible').attr('aria-live', 'assertive');
        isArrowKey = false;
        lastFocusedItem = null;
    });
    /* END Keyword auto suggestion - accessibilty for suggested list items */

    $('.keyword_suggest').on('blur', function(){
        $(this).attr('aria-expanded','false');
    }); 

    // For quick job search widget
    var form_uid = '.widget-jobsearch-v2';
    if ($(form_uid).length > 0) {

        // Remove [] from multiselect
        if($(form_uid).find('.text_select.multi').length > 0){
            $(form_uid).find('.text_select.multi').each(function(){
                let checkName = $(this).attr('name');    
                if( checkName.includes('[]') ){
                    let checkNameUpdate = checkName.replace('[]', '');
                    $(this).attr('name', checkNameUpdate);   
                }
            });
        }

        // Few required actions on form submit
        $(form_uid + ' form').submit(function() { 
            $(form_uid).find('.search-control-container').children('select').removeAttr('disabled');
            $(form_uid).find('.text_select.multi').each(function(){      
                    let checkName = $(this).attr('name');            
                    $(this).attr('name', checkName+'[]');   
            });
            $(form_uid).find('input[type="checkbox"]').each(function(){      
                let checkName = $(this).attr('name');            
                $(this).attr('name', checkName+'[]');   
            });            
        })

    }
    
    // To handle API call when Quick & Advanced job search are on same page
    let full_search_widget_class = $('.widget-jobsearch-full');
    let horizontal_search_widget_class = $('.widget-jobsearch-full-horizontal');
    let quick_search_widget_class = $('.widget-jobsearch-v2');
    
    if( (full_search_widget_class.length > 0 && quick_search_widget_class.length > 0) || (horizontal_search_widget_class.length > 0 && quick_search_widget_class.length > 0) ){
        
        quick_search_widget_class.find('select').each(function(){
            $(this).removeAttr('disabled');
            $(this).val('').trigger('change');
        })
        quick_search_widget_class.find('input[type="text"]').each(function(){
            $(this).val('');
        })
        quick_search_widget_class.find('input[type="checkbox"]').each(function(){
            $(this).prop('checked', false);
        })

    }
    
});

//Accessibility for search results table in mobile view
window.addEventListener('resize', function() {
    assignAARoles();
});
var currentView = 'table';
// Update roles on click between list and grid view
$(document).on('click', '#result-view-list', function() {
    currentView = 'table'; // user clicked list icon → treat as table layout
    setTimeout(assignAARoles, 100);
});
$(document).on('click', '#result-view-grid', function() {
    currentView = 'grid'; // user clicked grid icon → treat as list layout
    setTimeout(assignAARoles, 100);
});
var assignAARoles = function () {
    var isMobile = window.innerWidth < 769;
    var searchResultsWidgets = $('.widget-jobsearch-results, .widget-jobsearch-results.list, .widget-jobsearch-results.tiles');
    searchResultsWidgets.each(function() {
        var currentWidget = $(this);
        var currentTable = currentWidget.find('.search-results-table');
        if (currentTable.length > 0) {
            if(isMobile || currentView == 'grid'){
                    currentTable.removeAttr('role aria-busy aria-label');
                    currentTable.find('> div').removeAttr('role');
                    currentTable.find('.search-columns').removeAttr('role');
                    currentTable.find('.search-columns div').removeAttr('role');
                    currentTable.find('.entry-content-wrapper').not('.search-columns').attr({'role': 'list', 'aria-label': CWS._('Job Search Results')});
                    currentTable.find('.job').attr('role', 'listitem').removeAttr('aria-busy');
                    currentTable.find('.job-innerwrap').attr('role', 'list');
                    currentTable.find('.job-innerwrap > div').attr('role', 'listitem');
            }else{
                    currentTable.attr({'role': 'table', 'aria-busy': 'false'});
                    currentTable.find('> div').attr('role', 'rowgroup');
                    currentTable.find('.search-columns').attr('role', 'row');
                    currentTable.find('.search-columns div').attr('role', 'columnheader');
                    currentTable.find('.entry-content-wrapper').not('.search-columns').attr('role', 'rowgroup').removeAttr('aria-label');
                    currentTable.find('.job').attr({'role': 'row'}).removeAttr('aria-busy'); 
                    currentTable.find('.job-innerwrap').attr('role', 'presentation');
                    currentTable.find('.job-innerwrap > div').attr('role', 'cell');
                    currentTable.find('.job-innerwrap > div:first-child').attr('role', 'rowheader');
                    currentTable.attr('aria-label', CWS._('Job Search Results'));
            }
        }
    }); 
}

// Accessibility - Advanced Job Search widget (Default and Horizontal)

var widget_type = new Array('.widget-jobsearch-full', '.widget-jobsearch-full-horizontal', '.widget-jobsearch-v2');    

var combobox_accessibility = function(){

    widget_type.forEach(function (item, index) {

        // For Multiselect combobox
            
        if($(item).find('.text_select.multi').length > 0){

            $(item).find('.text_select.multi').each(function(index){

                $(this).parent('.search-control-container').find('.select2-search').append('<span id="search-help_'+ (index+1) +'" class="visually-hidden search-help-text sr-only" aria-live="polite">' + CWS._("Begin typing to find suggestions.") + '</span>');
                $(this).parent('.search-control-container').find('.select2-selection--multiple').find('input.select2-search__field').attr('aria-describedby', 'search-help_' +(index+1));

                var fieldLabel = '';

                if($(this).parents('.search-control-container').children('.w-form-row-label').find('label').length>0){
                    // For items with label								
                    fieldLabel = $(this).parents('.search-control-container').children('.w-form-row-label').find('label').text();
                }else if( $(this)[0].hasAttribute('placeholder') ){
                    // For items without label - placeholder only
                    fieldLabel = $(this).attr('placeholder');
                }
                    
                $(this).parent('.search-control-container').find('.select2-selection--multiple').removeAttr('role');
                $(this).parent('.search-control-container').find('.select2-selection--multiple').find('input.select2-search__field').attr('role', 'combobox');							
                $(this).parent('.search-control-container').find('.select2-selection--multiple').find('input.select2-search__field').attr('aria-expanded', 'false');							
                $(this).parent('.search-control-container').find('.select2-selection--multiple').find('input.select2-search__field').attr('aria-label', fieldLabel);
                $(this).parent('.search-control-container').find('.select2-selection--multiple').find('input.select2-search__field').attr('aria-haspopup', 'listbox');
                $(this).parent('.search-control-container').find('.select2-selection--multiple').removeAttr('aria-expanded');
                $(this).parent('.search-control-container').find('.select2-selection--multiple').removeAttr('aria-haspopup');
                $(this).parent('.search-control-container').find('.select2-selection--multiple').find('.select2-selection__rendered').removeAttr('tabindex');
                
                $(document).on('keyup click',function(e) {

                    if($('.select2.select2-container--open').length > 0){
                        $('.search-control-container').find('.select2-selection--multiple').find('input.select2-search__field').attr('aria-expanded', 'true');
                        $('.search-control-container').find('.select2-selection--multiple').removeAttr('aria-expanded');
                    }

                    $('.search-control-container').find('.select2-selection--multiple').on('focus blur', function() {
                        $(this).find('input.select2-search__field').attr('aria-expanded', 'false');
                        $(this).removeAttr('aria-expanded');
                    });

                });

                $(this).change(function(){
                    var opt=$(this).children('option:selected');
                    if (opt.length>0){
                        opt.each(function(){
                            $(this).parents('.search-control-container').find('li.select2-selection__choice').each(function(){
                                $(this).attr('aria-label', $(this).attr('title') + " selected ");
                                var title_text = $(this).attr('title');
                                var $removeBtn = $(this).find('.select2-selection__choice__remove');
                                if ($removeBtn.length) {
                                    $removeBtn.attr('aria-label', CWS._("Remove ") + title_text);
                                }
                            });
                        });
                    }                
                });

            });

        } 

        // For Select combobox (single)

        if( $(item).find('.text_select').parent('.search-control-container').find('.select2-selection--single').length > 0 ){
           
            $(item).find('.text_select').parent('.search-control-container').find('.select2-selection--single').each(function(){
                   
                var fieldLabel = '';

                if($(this).parents('.search-control-container').children('.w-form-row-label').find('label').length>0){

                    // For items with label
                    fieldLabel = $(this).parents('.search-control-container').children('.w-form-row-label').find('label').text();

                    $(this).attr('aria-label', fieldLabel);

                }else if( $(this).find('.select2-selection__rendered')[0].hasAttribute('title') ){

                    // For items without label - placeholder only
                    fieldLabel = $(this).find('.select2-selection__rendered').attr('title');
                        
                    $(this).parents('.search-control-container').find('select.text_select').change(function(){
                        // This is required as placeholder value is already there in span text and it is being read out by NVDA, if we put aria-label firsthand, it reads twice. Hence, we are adding on dropdown change only.
                        $(this).parents('.search-control-container').find('.select2-selection--single').attr('aria-label', fieldLabel);
                    });  

                }

                $(this).attr('aria-haspopup', 'listbox');
                $(this).removeAttr('aria-labelledby');
                $(this).attr('aria-disabled', 'false');
                $(this).find('.select2-selection__rendered').removeAttr('tabindex');
                $(this).find('.select2-selection__rendered').removeAttr('role');
                $(this).find('.select2-selection__rendered').removeAttr('aria-readonly');
                
            })
                
        } 
        $(item).find('.commute-control').each(function(){
            $(this).find('.select2-selection--single').removeAttr('aria-labelledby');
            $(this).find('.select2-selection__rendered').removeAttr('aria-readonly');
        })

        singleselect_accessibilty();

        // For Keyword Field

        if( $(item).find('.keyword_suggest').length > 0 ){
            $(item).find('.keyword_suggest').attr('role', 'combobox');
            $(item).find('.keyword_suggest').attr('aria-autocomplete', 'list');
            $(item).find('.keyword_suggest').attr('aria-expanded', 'false');
            $(item).find('.keyword_suggest').attr('aria-controls', 'ui-id-1');
            $('.ui-autocomplete').attr('role','listbox').removeAttr('tabindex');

            var label = $('label[for='+ $(item).find(".keyword_suggest").attr("id") +']').text();
            $('.ui-autocomplete').attr('aria-label', label);
        }

    })    

}

// For single select dropdowns fields - adding title attribute dynamically when the placeholder is empty.
var singleselect_accessibilty = function(){

    try{
        $('.search-control-container').find('.select2-selection--single').each(function(){
            if( !($(this).find('.select2-selection__rendered')[0].hasAttribute('title')) ){

                fieldTitle = $(this).parents('.search-control-container').children('.w-form-row-label').find('label').text();

                $(this).parents('.search-control-container').find('.select2-selection__rendered').attr('title', fieldTitle);

                $(this).parents('.search-control-container').find('select.text_select').change(function(){

                    fieldTitle = $(this).parents('.search-control-container').find('.select2-selection__rendered').text();
                    fieldTitle = fieldTitle ? fieldTitle : $(this).parents('.search-control-container').children('.w-form-row-label').find('label').text();

                    $(this).parents('.search-control-container').find('.select2-selection__rendered').attr('title', fieldTitle);
                });
            }
        });

        // Hide commute type and traffic Select2 containers from screen readers
       var commuteElements = $('#select2-cws_jobsearch_commute_type-container, #select2-cws_jobsearch_commute_traffic-container');
        if (commuteElements.length > 0) {
            commuteElements.attr({'aria-hidden': 'true', 'tabindex': '-1'})
            .removeAttr('title')
            .parent('.select2-selection.select2-selection--single')
            .removeAttr('title');
        }
        }
    catch(e){
        console.error(e);
    }
}

// Detect if the browser is Safari on MacOS
var isMacSafari = function () {
    var ua = window.navigator.userAgent;
    return ua.indexOf('Mac') !== -1 &&
        ua.indexOf('Safari') !== -1 &&
        ua.indexOf('Chrome') === -1;
};

// Select2 dropdown navigation accessibility - Using roving tabindex technique to read the combobox' options by managing focus.
var select2_navigation = function() {
    $('.text_select').on('select2:open', function (e) {
 
        var multiSelect = $(this).hasClass('multi');
        if(!multiSelect && $('#search-help').length <= 0) {
            $('.select2-search').append('<span id="search-help" class="visually-hidden sr-only" aria-live="polite">' + CWS._("Begin typing to find  suggestions.") + '</span>');
            $('.select2-dropdown').find('.select2-search__field').attr('aria-describedby', 'search-help');
        }

        var inputField = $('.select2-dropdown').find('.select2-search__field');
        var searchHelp = $('#search-help');
        if(multiSelect) {
            inputField = $(this).parent('.search-control-container').find('.select2-selection--multiple').find('input.select2-search__field');
            searchHelp = $(this).parent('.search-control-container').find('.select2-selection--multiple').find('input.select2-search__field').siblings('.search-help-text');
        }
 
        setTabindexOnOptions();
        
      // Fix VoiceOver skipping first option on macOS Safari
        var detectMacSafari = isMacSafari();
        if (detectMacSafari && multiSelect) {
            setTimeout(function () {
                var options = $('.select2-results__option[role="option"]').not('[aria-disabled="true"]');
                options.on('focus', function () {
                    var optionId = $(this).attr('id');
                    if (optionId) {
                        inputField.attr('aria-activedescendant', optionId);
                    }
                });

                var firstOption = options.first();
                if (firstOption.length) {
                    firstOption.addClass('select2-results__option--highlighted').attr('tabindex', '0').focus();
                    var firstOptionId = firstOption.attr('id');
                    if (firstOptionId) {
                        inputField.attr('aria-activedescendant', firstOptionId);
                    }
                }
            }, 0);
        }

        $('.select2-search__field').on('keydown', function (e) {
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                setTimeout(function() {
                    var highlighted = $('.select2-results__option.select2-results__option--highlighted[role="option"]:not([aria-disabled="true"])');
                    if (!highlighted.length && detectMacSafari && multiSelect) {
                        highlighted = $('.select2-results__option[role="option"]:not([aria-disabled="true"])').first().addClass('select2-results__option--highlighted').attr('tabindex', '0');
                    }
                    highlighted.focus();
                    if (detectMacSafari && multiSelect) {
                        var id = highlighted.attr('id');
                        if (id) {
                            inputField.attr('aria-activedescendant', id);
                        }
                    }
                }, 50);
            }
            else {
                if(inputField.val() && inputField.val().length > 0) {
                    searchHelp.text('');
                }
                // While searching, provide tabindex to the newly added options
                setTabindexOnOptions();
            }
        });

        function setTabindexOnOptions() {
            setTimeout(function() {
                var options = $('.select2-results__option[role="option"]').length;
                if(inputField.val() && inputField.val().length > 0) {
                    if (options > 0) {
                        searchHelp.text(options + CWS._(' options available, use up and down arrow keys to navigate'));
                    }
                    else {
                        searchHelp.text('');
                    }
                } else {
                    searchHelp.text(CWS._("Begin typing to find suggestions."));
                }
                $('.select2-results__option[role="option"]').attr('tabindex', '-1');
                $('.select2-results__option[role="option"]').first().attr('tabindex', '0');
                
                $('.select2-results__option[role="option"]').on('keydown', function (e) {
                    focusNextOption(e, $(this));
                });
            }, 500);
        }

        function focusNextOption(e, current) {
            let allOptions = $('.select2-results__option[role="option"]:not([aria-disabled="true"])');
            let currentIndex = allOptions.index(current);

            if (e.key === 'ArrowDown') {
                if (currentIndex < allOptions.length - 1) {
                    let next = allOptions.eq(currentIndex + 1);
                    moveFocus(current, next);
                }
            } else if (e.key === 'ArrowUp') {
                if (currentIndex > 0) {
                    let prev = allOptions.eq(currentIndex - 1);
                    moveFocus(current, prev);
                } else {
                    // Focus back to input field
                    current.attr('tabindex', '-1').removeClass('select2-results__option--highlighted');
                    inputField.focus();
                    inputField.removeAttr('aria-activedescendant');
                    setTabindexOnOptions();
                }
            } else if (e.key === 'Enter' || e.key === ' ') {
                current.trigger('mouseup'); // Selects the option
            } else if (e.key === 'Escape' || (e.key === 'Tab' && e.shiftKey)) {
                inputField.focus();
                searchHelp.text('');
                setTabindexOnOptions();
            }

            e.preventDefault();

            function moveFocus(from, to) {
                from.attr('tabindex', '-1').removeClass('select2-results__option--highlighted');
                to.attr('tabindex', '0').addClass('select2-results__option--highlighted').focus();
            }
        }
        /* Dynamically adding aria-controls attribute for select2 element, as refrence element(id) present after dropdown open */
        const selectElement = $(e.target).next('.select2-container').find('.select2-selection');
        const resultId = 'select2-' + $(e.target).data('select2-id') + '-results';
    
        selectElement.attr('aria-controls', resultId);
        /* END -- Dynamically adding aria-controls attribute for select2 element*/

    });
    /* Dynamically removing aria-controls attribute from select2 element, as refrence element(id) not present after dropdown close */
    $('.text_select').on('select2:close', function (e) {
        const selectElement = $(e.target).next('.select2-container').find('.select2-selection');
        selectElement.removeAttr('aria-controls');
    });
     /* END -- Dynamically removing aria-controls attribute from select2 element */
}

/*
 * This function enables the functionality of collapsible checkboxes.
 * Currently Expand/Collapse feature for search filter checkboxes will work only when it is enabled under sitewide settings.
 * Sothat it won't break existing custom scripts on some sites that are implemented for the same functionality.
 */
function searchFiltersCollapsibleCheckboxes() {
    $(".filter-checkbox-wrapper").hide();
    $(".search-checkbox-container").addClass('closed');
    $(".search-checkbox-title").addClass('collapse_expand_filter_title closed');
    $(".filter-checkbox-wrapper").addClass('closed');

    $(".search-checkbox-title").attr({'tabindex': 0, 'role': 'button', 'aria-expanded': 'false'});

    $(".search-checkbox-title").click(function(){
        $(this).siblings('.filter-checkbox-wrapper').toggle();
        if( $(this).hasClass('open') ) {
            $(this).addClass('closed');
            $(this).removeClass('open');
            $(this).attr('aria-expanded','false');
        } else {
            $(this).addClass('open');
            $(this).removeClass('closed');
            $(this).attr('aria-expanded','true');
        }
    });

    $(".search-checkbox-container").each(function(){
        if( $(this).find(".search-checkbox-item input").is(':checked')) {
            $(this).find('.filter-checkbox-wrapper').show();
            $( this ).find('.search-checkbox-title').addClass( "open" );
            $( this ).find('.search-checkbox-title').removeClass( "closed" );
            $(this).find('.search-checkbox-title').attr('aria-expanded','true');
        }
    });
}

var multi_checkbox = function (widget_form_id) {
    let form_uid = 'form#'+widget_form_id;
    if ($(form_uid).length > 0) {
        if($(form_uid).find('.search-checkbox-item').length > 0){
            $(form_uid).find('.search-checkbox-item').each(function(){
                if( !$(this).children('input[type=checkbox]').attr('name').includes('[]') ){
                    let checkName = $(this).children('input[type=checkbox]').attr('name');
                    let checkNameUpdate = checkName + '[]';
                    $(this).children('input[type=checkbox]').attr('name', checkNameUpdate);
                }   
            });
        }
    }        
}; 

CWS.jobs = (function (w, $) {
    // Private variables and functions
    var options = {
            org_id: '68',
            search_on_pause: false,
            search_on_blur: true,
            disable_fuzzy: false,
            display_loading_bar: true,
            show_column_headers: true,
            results_error: 'There was a problem processing your search, please try again.',
            results_none: 'No jobs match your search criteria.',
           // location_invalid_visible: CWS._('Error: Please enter and select a valid location'),
            location_invalid_visible: CWS._('Error: Please select a valid location from dropdown'),
            location_invalid_read: CWS._('Error please enter and select a valid location'),
            limit: 12,
            max_pages: 6,
            jobdetail_path: 'job-description',
            pollinator_cp: '',
            pollinator_noresults: false,
            pollinator_noresults_text: '',
            pollinator_noresults_link: 'Join our Talent Network',
            locations_page: false,
            locations_page_radius: 25,
            locations_page_search_by: 'radius',
            columns: '',
            column_spans: '',
            filters: [],
			filter_od: '',
            set_location_error_flase: false,
            node : 0,
            boost: false,
            use_boolean_search: false,
            job_custom_css: '',
            tag_criteria: false,
            dynamic_criteria: false,
            inc_facetcount : false,
            view_by_layout: 'original',
            mapPosition:'right',
            hide_pagination_job_list: false,
            job_apply_button_text: 'Apply',
            jobs_description_limit_words: 0
        },

        columns = '',
        column_spans = '',
        column_labels = '',
        include_country ='',

        page = 0,
        api_url = '//jobsapi-internal.m-cloud.io/api/',
        parent,
        //widgetDiv = $('.widget-jobsearch-full-horizontal').length > 0 ? 'widget-jobsearch-full-horizontal' : 'widget-jobsearch-full',
        results = $('#widget-jobsearch-results-list'),
        loader,
        pages = $('#widget-jobsearch-results-pages'),
        total_jobs = 0,
        current_jobs = {},
        sortfield = 'open_date',
        sortorder = 'descending',
        auto_titles = [],
        autocomplete,
        autocomplete_service,
        location_criteria,
        pageloaded = false,
        old_keywords = '',
        focus_on_first_result = false,
        no_form = false,
        last_place,
        // We currently store the full state name (rather than code) for countries outside of the ones in this variable;
        // If the country is not in this variable, it'll use a radius search instead
        statewide_whitelist = CWS.apply_filter('statewide_whitelist', {'US': true, 'CA': true});
        if($('.widget-jobsearch-full-horizontal').length > 0){
            widgetDiv = 'widget-jobsearch-full-horizontal';
        }else if($('.widget-jobsearch-full').length > 0){
            widgetDiv = 'widget-jobsearch-full';
        }else{
            widgetDiv = 'widget-jobsearch-v2';
        }

    var clear_counter = 0;
    var originating_search_event = null;
    var default_sortfield = 'open_date';
    var default_sortorder = 'descending';

    var search_jobs = function (criteria, pollinator_data, originating_event) {
        // Just in case
        criteria = criteria || {};

        // Used later in job_callback()
        originating_search_event = originating_event;

        refresh_column_sort();

        if(options.tag_criteria) {
            var criteria_tags = criteria;
            refresh_tags(criteria_tags, originating_event);
        }

        // Sort by date if there's no other criteria, otherwise sort by relevance
        if (sortfield && !criteria.hasOwnProperty('sortfield')) {
            criteria.sortfield = sortfield;
            criteria.sortorder = sortorder;
        }

        // Check if there are any sitewide filters such as language
        if (options.filters.length > 0) {
            if (criteria['facet']) {
                // Underscore's union() removes duplicate keys... probably not necessary but it's cleaner
                criteria['facet'] = _.union(criteria['facet'], options.filters);
            }
            else {
                // No existing filters from the search form exist
                criteria['facet'] = options.filters;
            }
        }

        if(options.filter_od){
            criteria.openeddate = options.filter_od;
        }

        if (options.node) {
            criteria['node'] = options.node;
        }

        if (options.boost) {
            criteria['boost'] = options.boost;
        }

        criteria.Limit = options.limit;
        criteria.Organization = options.org_id;
        if (!criteria.hasOwnProperty('offset')) {
            criteria.offset = page * options.limit + 1;
        }

        if (options.disable_fuzzy) {
            criteria.fuzzy = 'false';
        }

        if (options.use_boolean_search) {
            criteria.useBooleanKeywordSearch = 'true';
        }

        if(cws_opts && cws_opts.personalization && !CWS.depersonalize){
            save_last_search(criteria);
        }

        if (options.display_loading_bar && $('#loader').length !== 0) {
            loader.start();
            $('.widget-jobsearch-results').find('.search-results-table').attr("aria-busy","true");
        }

        //Commenting out this code as we are picking all drop down lists from the widget.
        /*if (options.dynamic_criteria && criteria.hasOwnProperty('facet')) {
            criteria.facetlist = [];
            $('select[data-facet]').each(function(){
                criteria.facetlist.push($(this).data('facet'));
            });
        }*/

        if (options.dynamic_criteria) {
            if(!criteria.facetlist) {
                criteria.facetlist = [];
            }
            //This is how the facetlist should look like
            //criteria.facetlist = ["primary_city", "primary_state", "primary_category", "industry", "sub_category"];
            //Picking out all dropdowns from widget
            $('.'+widget_jobsearch_full_horizontal + ' select').each(function(i, obj) {
                if(!criteria.facetlist.includes( $(obj).data('facet') )) {
                    criteria.facetlist.push( $(obj).data('facet') );
                }
            });

            $('.'+widget_jobsearch_full_horizontal + ' .search-checkbox-container').each(function(i, obj) {
                if(!criteria.facetlist.includes($(obj).data('facet'))) {
                    criteria.facetlist.push($(obj).data('facet'));
                }
            });

            last_clicked_search_field();
            
            if(clear_counter == 0){
                clear_search_field();
                clear_counter++;
            }

        }

        // Whenever SearchText contains '&', we need to treat it as a boolean search to get the accurate results, 
        // and send the SearchText in quotes if fuzzy is false(keyword selected from suggestions) to get exactly matched jobs.
        if(!cws_opts.api.includes('google') && criteria.hasOwnProperty('SearchText') && criteria.SearchText.includes('&')) {
            criteria.useBooleanKeywordSearch = 'true';
            if(criteria.fuzzy == 'false') {
                criteria.SearchText = `"${criteria.SearchText}"`;
            }
        }

        var map_criteria = criteria; // While using Google maps, we need to pass the same criteria to the map search - otherwise it is generating duplicate customAttributeFilter value for the payload.
        criteria = CWS.apply_filter('search_criteria', criteria);
        var api = CWS.apply_filter('search_url', api_url + 'job');

        //CWS.aria_live('Searching for jobs.');

        $.ajax(CWS.apply_filter('search_ajax', {

            url: api,
            data: criteria,
            dataType: 'jsonp',
            crossDomain: true,
            cache: !CWS.isIE(), // IE9 and below have problems caching ajax requests, sigh
            jsonpCallback: 'CWS.jobs.jobCallback',
            error: function (jqXHR, textStatus, errorThrown) {
                if(jqXHR.status === 200 || jqXHR.status === 201 || jqXHR.status === 202 || jqXHR.status === 204){
                    return true;
                }

                // Errors messages will be viewable via inspector tools
                results.html(options.results_error + '<span style="display:none;">' + textStatus + ' -- ' + errorThrown + '</span>');

                // Resets the dropdowns
                if(options.dynamic_criteria) {
                    let undef; // stores the value 'undefined'
                    refresh_dropdowns(undef, combobox_accessibility);
                }
            },
            complete: function () {
                if (CWS.map) {
                    CWS.map.search(map_criteria, loader);
                }
                else if (options.display_loading_bar && $('#loader').length !== 0) {
                    loader.end();
                    $('.widget-jobsearch-results').find('.search-results-table').attr("aria-busy","false");
                }
            }
        }));
    };

    var refresh_current_filter = function(criteria, current_event){

        let current_filter = $('select.opendropdown').data('facet');
        criteria = criteria || {};
        // Check if there are any sitewide filters such as language
        if (options.filters.length > 0) {
            if (criteria['facet']) {
                // Underscore's union() removes duplicate keys... probably not necessary but it's cleaner
                criteria['facet'] = _.union(criteria['facet'], options.filters);
            }
            else {
                // No existing filters from the search form exist
                criteria['facet'] = options.filters;
            }
        }
        if(options.filter_od){
            criteria.openeddate = options.filter_od;
        }

        if (options.node) {
            criteria['node'] = options.node;
        }

        if (options.boost) {
            criteria['boost'] = options.boost;
        }

        criteria.Organization = options.org_id;

        if (options.disable_fuzzy) {
            criteria.fuzzy = 'false';
        }

        if (options.use_boolean_search) {
            criteria.useBooleanKeywordSearch = 'true';
        }

        criteria.facetlist = [current_filter];
        
        
        // Remove items containing the current filter facet name
        if(criteria.facet && criteria.facet.length > 0){
            criteria.facet = jQuery.grep(criteria.facet, function(item) {
                return item.indexOf(current_filter) === -1; // Keep items that do not contain "curernt facet"
            });
        }

        if(!cws_opts.api.includes('google') && criteria.hasOwnProperty('SearchText') && criteria.SearchText.includes('&')) {
            criteria.useBooleanKeywordSearch = 'true';
            if(criteria.fuzzy == 'false') {
                criteria.SearchText = `"${criteria.SearchText}"`;
            }
        }

        criteria = CWS.apply_filter('search_criteria', criteria);
        var api = CWS.apply_filter('search_url', api_url + 'job');
        
        $.ajax(CWS.apply_filter('search_ajax', {
            url: api,
            data: criteria,
            dataType: 'jsonp',
            crossDomain: true,
            cache: !CWS.isIE(), // IE9 and below have problems caching ajax requests, sigh
            jsonpCallback: 'CWS.jobs.jobCallbackRefreshFilter',
            error: function (jqXHR, textStatus, errorThrown) {
                if(jqXHR.status === 200 || jqXHR.status === 201 || jqXHR.status === 202 || jqXHR.status === 204){
                    return true;
                }
                // Resets the dropdowns
                if(options.dynamic_criteria) {
                    let undef; // stores the value 'undefined'
                    refresh_current_dropdowns(undef);
                }
            }
        }));
    };
    var job_refresh_filter  = function (data){       
        data = CWS.apply_filter('search_response', data);
        var aggregation = {};
        if(options.dynamic_criteria){
            if(data.hasOwnProperty('histogramResults')) {
                //structuring data from google jobs to smartpost response as our code is structured that way
                $.each(data['histogramResults'], function (index, value) {
                    aggregation[value.field] = {};
                    aggregation[value.field]["buckets"] = [];
                    $.each(value.values, function (index, val) {
                        aggregation[value.field]["buckets"].push(
                            {
                                "key" : index,
                                "doc_count" : val
                            }
                        );
                    });
                });
            }
            if(data.hasOwnProperty('aggregations')) {
                aggregation = data.aggregations;
            }
            refresh_current_dropdowns(aggregation);
        }
    }
    var job_callback = function(data) {
        data = CWS.apply_filter('search_response', data);
        display_jobs(data);

        if(originating_search_event && originating_search_event.target && originating_search_event.target.id === 'cws-adv-search-btn'){
            if(originating_search_event.type === 'click')
                {
                    if($("h2").hasClass("search-results-title")) {
                        $('.search-results-title').focus();
                    }else{
                        $('#live-results').focus();
                    }
                }
        }

        // Reset global variable
        originating_search_event = null;
       
        var aggregation = {};
        if(options.dynamic_criteria){
            if(data.hasOwnProperty('histogramResults')) {
                //structuring data from google jobs to smartpost response as our code is structured that way
                $.each(data['histogramResults'], function (index, value) {
                    aggregation[value.field] = {};
                    aggregation[value.field]["buckets"] = [];
                    $.each(value.values, function (index, val) {
                        aggregation[value.field]["buckets"].push(
                            {
                                "key" : index,
                                "doc_count" : val
                            }
                        );
                    });
                });
            }
            if(data.hasOwnProperty('aggregations')) {
                aggregation = data.aggregations;
            }
            refresh_dropdowns(aggregation, combobox_accessibility);
        } 
        assignAARoles();
    };

    var gather_criteria = function (e, location_changed,refresh_open_filter=false) {
        if ((e && e.keyCode == 13) // enter key
            || (e && e.type == 'click') // button
            || (e && e.type == 'change') // locatoin suggest
            || (e && e.type == 'slidechange') // date slider
            || (e && e.type == 'autocompleteselect') // keyword suggest
            || (e && refresh_open_filter == true) // refresh dropdown in chaining search
            || !e) {
            var criteria = {};
            var querystring = {};
            widget_jobsearch_full_horizontal = widgetDiv;

            if (e) {
                page = 0;

                /**
                 * Here's the fuzzy logic. If you select a keyword suggestion, set fuzzy to false.
                 * If other fields changed but not keywords, leave fuzzy as false.
                 * If the keyword field changes but is not because of a keyword suggestion select, remove the fuzzy = false.
                 */
                var keyword = $('#cws_jobsearch_keywords'),
                    fuzzy = $('#cws_jobsearch_fuzzy');

                if (e.type == 'autocompleteselect') {
                    // I prevent event propagation so that the onchange event wouldn't get triggered after selecting a suggested keyword
                    // Because of that, the .each() below does not pick up the new keyword value
                    keyword.val(e.ui.item.value);
                    fuzzy.val('false');
                }
                else if ($('#job-live-search').length && !$('#job-live-search').is(':checked') && fuzzy.attr('data-search') == 'true') {
                    // Handling fuzzy logic when live-search toggle is disabled on front-end
                    fuzzy.val('false');
                    fuzzy.removeAttr('data-search');
                }
                else if (keyword.val() !== old_keywords) {
                    fuzzy.val('');
                }

                old_keywords = keyword.val();
            }

            // Page is a little bit unique. Let's sync the hidden field with our internal var here
            if (page !== 0) {
                $('#cws-search-page').val(page + 1);
            }
            else {
                $('#cws-search-page').val('');
            }

            // Close pollinator after form submission by default
            var pollinator_data = {cp: options.pollinator_cp, d: 'fnCLOSE'};

            $('.'+ widget_jobsearch_full_horizontal +' .clear-btn').remove();

            // To handle double API call when Quick & Advanced job search are on same page
            let full_search_widget_class = $('.widget-jobsearch-full');
            let horizontal_search_widget_class = $('.widget-jobsearch-full-horizontal');
            let quick_search_widget_class = $('.widget-jobsearch-v2');
            let quick_job_search_widget = null;
            
            if( (!full_search_widget_class.length > 0 && quick_search_widget_class.length > 0) && (!horizontal_search_widget_class.length > 0 && quick_search_widget_class.length > 0) ){
                quick_job_search_widget = '.widget-jobsearch-v2';
            }

            $('.'+widget_jobsearch_full_horizontal+', .global-search-and-filters-section, '+ quick_job_search_widget).find('input[type=text],input[type=hidden],select,.unit-switch:checked').each(function () {
                // Each form field will have a data-param attribute to tell it what API parameter it is for
                var key = $(this).data('param'),
                    facet = $(this).data('facet'),
                    name = $(this).attr('name'),
                    val = $(this).val(),
                    placeholder = $(this).attr('placeholder'),
                    is_multi = $(this).hasClass('multi'),
                    nationwide = false;
                if (!is_multi) {
                    // Multi select comes back as an array, which we want. Trim turns it into a string.
                    val = $.trim(val);
                }
                else if (val === null){
                    val = '';
                }

                // Accessible sites have clear links under fields with content, let's clear them first as a precaution.
                if (cws_opts && cws_opts.accessible) {
                    if (val !== '' && val !== placeholder && key !== 'LocationRadius' && !$(this).is('[type=hidden]')) {
                        var label_el = $(this).siblings('label').length > 0 ? $(this).siblings('label') : $(this).parent().siblings('label');
                        if (label_el.length > 0) {
                            // Might be multiple labels next to each other in the DOM
                            if (label_el.length > 1) {
                                label_el = label_el.first();
                            }

                            var txt = label_el.text();
                            // var lbl = txt == 'Select a Location' ? 'location' : txt;
                            var lbl = $(this).prev().find('label').text();

                            if(lbl.length === 0){
                                lbl = $(this).parent().prev().find('label').text();
                            }

                            // $(this).after('<a href="#" onclick="CWS.jobs.clear_field(this); return false;" class="clear-btn" aria-label="' + CWS._('Clear the') + ' ' + lbl + '">' + CWS._('Clear') + '</a>');
                            // $(this).after('<a href="#" onclick="CWS.jobs.clear_field(this); return false;" class="clear-btn" aria-label="' + CWS._('Clear') + ' ' + lbl + ' ' + CWS._('Selection') + '">' + CWS._('Clear') + ' ' + lbl + ' ' + CWS._('Selection') + '</a>');
                            $(this).after('<a href="#" onclick="CWS.jobs.clear_field(this); return false;" class="clear-btn" >' + CWS._('Clear') + ' ' + lbl + ' ' + CWS._('Selection') + '</a>');
                        }
                    }
                }

                if (key) {
                    if (facet) {
                        if (val !== '' && val !== placeholder) {
                            if (!criteria[key]) {
                                criteria[key] = [];
                            }
                            if(typeof val === 'string') {
                                criteria[key].push(facet + ':' + val);
                            }
                            else if(typeof val === 'object' && val.length){
                                criteria[key].push(facet + ':' + val.join('~'));
                            }
                            querystring[name] = $(this).val();

                            if (facet == 'primary_category') {
                                // "General Title", could be overwritten by a keyword search... seems logical to me
                                pollinator_data['gj'] = $(this).val();
                            }
                        }
                    }
                    else if (val && val != placeholder) {
                        if (key === 'offset') {
                            criteria[key] = page * options.limit + 1;
                            querystring[name] = val;
                        }
                        else {
                            criteria[key] = val;
                            querystring[name] = val;

                            // Hardcoded conditional statements, everything I've tried to avoid, but there's no time left.
                            if (key == 'SearchText') {
                                pollinator_data['gj'] = val;

                                // Remove sorting if there's a keyword and submit event
                                if (e) {
                                    sortfield = '';
                                    $('#cws-search-sortfield').val('');
                                    $('#cws-search-direction').val('');
                                    delete querystring['sort'];
                                    delete querystring['dir'];
                                }

                                // SearchText currently does not support searching by job id
                                // Not a great solution.... may cause issues with a category selected.
                                // TODO: use similar custom querystring builder in class-cws-search-options
                                /* removing as api should be searching against ids
                                 var job_id_test = parseInt(val);
                                 if (job_id_test) {
                                 criteria['facet'] = 'id:' + job_id_test;
                                 delete criteria.SearchText;
                                 }
                                 */
                            }
                        }
                    }
                }
            });

            if ($('#date-slider').length > 0) {
                var val = $('#date-slider').slider('value'),
                    now = new Date(),
                    tfh = 1000 * 60 * 60 * 24,
                    msg = 'Posted date, ';

                if (val === 1) {
                    criteria.openeddate = new Date(now.getTime() - tfh).toISOString();
                    msg += 'within the last twenty four hours, use right arrow to increase';
                }
                else if (val === 2) {
                    criteria.openeddate = new Date(now.getTime() - (tfh * 7)).toISOString();
                    msg += 'within the last seven days, use left and right arrow keys to change';
                }
                else if (val === 3) {
                    criteria.openeddate = new Date(now.getTime() - (tfh * 30)).toISOString();
                    msg += 'within the last thirty days, use left and right arrow keys to change';
                }
                else {
                    msg += 'any time, use left arrow to reduce';
                }

                if (e && e.type == 'slidechange') {
                    $('#date-slider .ui-slider-handle').attr('aria-label',msg);
                }
            }

            else if($('#date-container.radios').length > 0){
                var val = $('#date-container.radios input:checked').val();
                now = new Date(),
                    tfh = 1000 * 60 * 60 * 24;

                var days = {'1': 1, '2': 7, '3': 30};
                if(val !== '4' && val != ''){
                    criteria.openeddate = new Date(now.getTime() - (tfh * days[val])).toISOString();
                }
            }

            // Checkbox groups are handled a bit differently
            var checkbox_groups = {};
            $('.'+widget_jobsearch_full_horizontal).find('input[type=checkbox]:checked:not(.unit-switch):not(#job-live-search)').each(function () {
                var $this = $(this);
                if($this.data('param') !== 'facet') {
                    var name = $this.attr('name');
                    if (criteria[name]) {
                        criteria[name] += ',' + $this.val();
                        querystring[name] += ',' + $this.val();
                    } else {
                        criteria[name] = $this.val();
                        querystring[name] = $this.val();
                    }

                    if ($this.val() === 'Nationwide') {
                        nationwide = true;
                    }
                }
                else {
                    if(!checkbox_groups.hasOwnProperty($this.data('facet'))){
                        checkbox_groups[$this.data('facet')] = {'name': $this.attr('name'), 'values': []};
                    }
                    checkbox_groups[$this.data('facet')]['values'].push($this.val());
                }
            });

            for(var facet_name in checkbox_groups) {
                if(!criteria['facet']) {
                    criteria['facet'] = [];
                }
                criteria['facet'].push(facet_name + ':' + checkbox_groups[facet_name]['values'].join('~'));
                querystring[checkbox_groups[facet_name]['name']] = checkbox_groups[facet_name]['values'];
            }
            // The location field is handled WAY different.
            // Need to check if it's changed but we're missing lat/lon coordinates
            if (criteria.hasOwnProperty('Location') && criteria.Location && criteria.Location != 'Location' ) {
                // Needs to do a quick check to see if location is set as a facet, and that's an array, go go underscore
                var contains_state_or_country = false;
                if (criteria.facet) { // necessary null check
                    contains_state_or_country = _.some(criteria.facet, function (item) {
                        // state or country, we don't know what their values are either way
                        return (item.indexOf('primary_country') >= 0 || item.indexOf('primary_state') >= 0);
                    });
                }
                // location_changed should only be true from an autocomplete event
                if (location_changed === true) {
                    get_location(criteria, pollinator_data, querystring);
                }

                // There's a location property but no lat/lng, AS WELL AS no whole state/country, get coords from Google
                else if (( !criteria.hasOwnProperty('latitude') || !criteria.hasOwnProperty('longitude') ) && contains_state_or_country === false) {
                    //sortfield = '';
                    get_location(criteria, pollinator_data, querystring);
                }
                else {
                    pollinator_data['gl'] = criteria.Location;
                    delete criteria.Location;

                    /* Switching whole country to use countryStateCity
                    if(contains_state_or_country){
                        for(var f = 0, flen = criteria.facet.length; f < flen; f++){
                            if(criteria.facet[f].indexOf('primary_country') > -1){
                                criteria['countryStateCity'] = criteria.facet[f].split(':')[1];
                                delete criteria.facet[f];
                                break;
                            }
                        }
                    }*/

                    // Switching whole country to use countryStateCity
                    if(contains_state_or_country){
                        var new_facets = [];

                        var state, country;

                        for(var f = 0, flen = criteria.facet.length; f < flen; f++){
                            if(criteria.facet[f].indexOf('primary_country') > -1){
                                country = criteria.facet[f].split(':')[1];
                            }
                            else if(criteria.facet[f].indexOf('primary_state') > -1){
                                state = criteria.facet[f].split(':')[1];
                            }
                            else{
                                new_facets.push(criteria.facet[f]);
                            }
                        }

                        // Replace old facet array with the one that does NOT have country/state in it
                        criteria.facet = new_facets;

                        if(country){
                            criteria['countryStateCity'] = country;
                            if(state){
                                criteria['countryStateCity'] += ',' + state;
                            }
                        }
                        $('#cws_jobsearch__proximity').attr('disabled', 'disabled');
                    }else{
                        $('#cws_jobsearch__proximity').removeAttr('disabled');
                    }

                    // This prevents a duplicate GTM tag from firing off on page load
                    if (pageloaded !== false) {
                        querystring = CWS.apply_filter('search_results_querystring', querystring);
                        History.replaceState(null, document.title, CWS.build_querystring(querystring));
                    }
                    pageloaded = true;

                    if(refresh_open_filter==true){
                        refresh_current_filter(criteria, e);
                        return true;
                    }
                    search_jobs(criteria, pollinator_data, e);
                }
            }
            else {
                // Don't include radius if there is no location
                delete querystring.radius;
                delete criteria.LocationRadius;
                delete querystring.units;
                delete criteria.locationunits;

                // Also look for IE8 placeholders
                // Gross repetitive if-statement
                if (criteria.hasOwnProperty('Location') && criteria.Location == 'Location') {
                    delete criteria.Location;
                    delete querystring.Location;
                }

                $('#cws_jobsearch_latitude').val('');
                $('#cws_jobsearch_longitude').val('');

                var country = $('#cws_jobsearch_country'),
                    state = $('#cws_jobsearch_state');
                if (country.length > 0) {
                    delete querystring.country;
                    delete querystring.state;
                    delete criteria.nationwideCountries;
                    delete criteria.statewideStates;
                    delete criteria.countryStateCity;
                    if (criteria.facet) {
                        // facets are an array so we can delete like we did with the others
                        var new_facets = [];
                        for (var i = 0, len = criteria.facet.length; i < len; i++) {
                            if (criteria.facet[i] !== 'primary_country:' + country.val() &&
                                criteria.facet[i] !== 'primary_state:' + state.val()) {
                                new_facets.push(criteria.facet[i]);
                            }
                        }
                        criteria.facet = new_facets;
                    }

                    country.val('');
                    state.val('');
                }

                delete criteria.Latitude;
                delete criteria.Longitude;
                delete querystring.latitude;
                delete querystring.longitude;

                $('#cws_jobsearch_location').attr('aria-invalid', 'false').parent().removeClass('error');
                $('.location-wrapper .error-msg').remove();

                $('#cws_jobsearch__proximity').removeAttr('disabled');

                // This prevents a duplicate GTM tag from firing off on page load
                if (pageloaded !== false) {
                    querystring = CWS.apply_filter('search_results_querystring', querystring);
                    History.replaceState(null, document.title, CWS.build_querystring(querystring));
                }
                pageloaded = true;

                if (location_criteria) {
                    criteria = _.clone(location_criteria);
                }
                if(refresh_open_filter==true){
                    refresh_current_filter(criteria, e);
                    return true;
                }
                search_jobs(criteria, pollinator_data, e);
            }

            // Enable event propagation in case of quicksearch widget
            var widget_class = $('.widget-jobsearch-v2');
            if (!widget_class.length > 0) {
                return false;
            }         

        }
    };

    var display_jobs = function (data,  pollinator_data = null) {
        var html = '';
        data = data || current_jobs;
        var focus_el = '#widget-jobsearch-results-list .job:first-child a';

        if (data) {

            // For pagination
            total_jobs = data.totalHits || 0;
            current_jobs = data;

            // If hide pagination in Job list is set to true, then Live result will always be same as options.limit
            // (Default: 10, else custom set value from jobs per page options in widget settings)


            var jobCount = CWS.apply_filter('search_total', (!options.hide_pagination_job_list) ? total_jobs : ((total_jobs < options.limit) ? total_jobs : options.limit));
            $('#live-results-counter').html(jobCount);
            // Update the dedicated hidden live region with clear-then-set so NVDA re-announces
            // even when the count hasn't changed.
            var liveText = jobCount + ' Live Results';
            $('#live-results-aria').html('');
            setTimeout(function() {
                $('#live-results-aria').html(liveText);
            }, 50);

            if (data.totalHits && data.totalHits > 0) {
                // show global search results section.
                $('.global-job-results').css('display', 'block');
                $('#global_search_results_count').html(total_jobs + cws_opts.globalSearch_resultsCount + ' ');

                var cols = columns.split(',');
                var col_spans = column_spans.split(',');
                var col_labels = column_labels.split(',');

                // We don't want to do an unecessary string replace for non-accessible sites
                var aria_replace = cws_opts && cws_opts.accessible ? ' {aria_title}' : '';

                for (var i = 0, len = data.queryResult.length; i < len; i++) {
                    var job = data.queryResult[i];
                    var posted = Date.fromISO(job.open_date);
                    var joburl = options.jobdetail_path + '/' + job.id + '/' + seo_url_text(job);
                    joburl = CWS.apply_filter('search_result_job_url', joburl, job);
                    var secondary_locations = '';

                    // Accessibility title, may or not be used
                    var aria_message = 'Job number ' + (i + 1) + ': ' + job.title;
                    var aria_formatted_date = CWS.months[posted.getMonth()] + ' ' + posted.getDate() + ', ' + posted.getFullYear();
                    var aria_formatted_state = job.primary_city + (job.primary_state ? ', ' + (CWS.states.hasOwnProperty(job.primary_state) ? CWS.states[job.primary_state] : job.primary_state) : (job.primary_country ? ', ' + job.primary_country : ''));

                    var formatted = (posted.getMonth() + 1) + '/' + posted.getDate() + '/' + posted.getFullYear();
                    switch(cws_opts.search_date_format){
                        case 'M/D/YYYY':
                            formatted = (posted.getMonth() + 1) + '/' + posted.getDate() + '/' + posted.getFullYear();
                            break;
                        case 'M-D-YYYY':
                            formatted = (posted.getMonth() + 1) + '-' + posted.getDate() + '-' + posted.getFullYear();
                            break;
                        case 'D/M/YYYY':
                            formatted = posted.getDate() + '/' + (posted.getMonth() + 1) + '/' + posted.getFullYear();
                            break;
                        case 'D-M-YYYY':
                            formatted = posted.getDate() + '-' + (posted.getMonth() + 1) + '-' + posted.getFullYear();
                            break;
                        case 'toLocale':
                            // For IE, toLocaleDateString() is only supported IE 11+
                            try {
                                formatted = posted.toLocaleDateString();
                            }
                            catch (ex) { }
                            break;
                        case 'Mo D, YYYY':
                            formatted = CWS.short_months[posted.getMonth()] + ' ' + posted.getDate() + ', ' + posted.getFullYear();
                            break;
                        case 'Month D, YYYY':
                            formatted = CWS.months[posted.getMonth()] + ' ' + posted.getDate() + ', ' + posted.getFullYear();
                            break;
                    }
                    formatted = CWS.apply_filter('result_job_date', formatted, job);

                    if (job.hasOwnProperty('addtnl_locations') && job.addtnl_locations.length > 0) {
                        for (var z = 0, lenz = job.addtnl_locations.length; z < lenz; z++) {
                            if (job.addtnl_locations[z].addtnl_city !== job.primary_city) {
                                secondary_locations += '<div class="child addtnl_loc">';
                                secondary_locations += job.addtnl_locations[z].addtnl_city + (job.addtnl_locations[z].addtnl_state ? ', ' + job.addtnl_locations[z].addtnl_state : '');
                                if(include_country){
                                    secondary_locations += (job.addtnl_locations[z].addtnl_country ? ', ' + job.addtnl_locations[z].addtnl_country : '');
                                }
                                secondary_locations += '</div>';
                            }
                        }
                    }
                    var custom_job_wrapper_class = job.hasOwnProperty(options.job_custom_css) ? string_to_css_class(job[options.job_custom_css]) : '';
                    custom_job_wrapper_class = CWS.apply_filter('job_container_class', custom_job_wrapper_class);
                    var tableListRole = $('.search-results-table').attr("role");
                    var rowListItem = (tableListRole == "table" ? 'role="row"' : 'role="listitem"');
                    var cellListItem = (tableListRole == "table" ? 'role="cell"' : 'role="listitem"');
                    var job_html = '<div '+ rowListItem + '' + (tableListRole == "table" ? 'aria-busy="true"' : '') + ' class="job clearfix' + (i % 2 == 0 ? '' : ' alt') + ' ' + custom_job_wrapper_class + ' jobid-' + job.id + '" onclick="CWS.jobs.go_to_job(this);"><div class="job-innerwrap g-cols" ' + (tableListRole == "table" ? 'role="presentation"' : 'role="list"') + '>';
                    job_html = CWS.apply_filter('before_job_columns', job_html, job);

                    for (j = 0, len2 = cols.length; j < len2; j++) {
                        var col_class = (col_spans[j] ? col_spans[j] : '' );
                        var first_last_class = (j === 0 ? ' ' + CWS.layout_builder.columns.first : '') + (j === len2 ? ' ' + CWS.layout_builder.columns.last : '');
                        col_class += first_last_class;

                        if (cols[j] === 'title') {
                            job_html += '<div '+ cellListItem +' class="flex_column ' + col_class + '">';
                            job_html += '<div class="propic-wrapper"><div class="pro-pic"></div></div>';
                        }
                        else if (cols[j] === 'title_category') {
                            job_html += '<div '+ cellListItem +' class="flex_column ' + col_class + '">';
                            job_html += '<div class="propic-wrapper"><div class="pro-pic"></div></div>';
                        }
                        else if(cols[j].indexOf('city_state')>=0){
                            job_html += '<div '+ cellListItem +' class="flex_column joblist-location ' + col_class + '">';
                        }
                        else if(cols[j] === 'open_date'){
                            job_html += '<div '+ cellListItem +' class="flex_column joblist-posdate ' + col_class + '">';
                        }
                        else{
                            job_html += '<div '+ cellListItem +' class="flex_column ' + col_class + '">';
                        }
                        if (cols[j] === 'title') {
                            // job_html += '<div class="jobTitle"><a title="'+ job.title +'" href="' + CWS.apply_filter('result_job_url', joburl) + '"' + aria_replace + ' id="job-result' + i + '">' + CWS.apply_filter('result_job_title', job.title) + '</a></div>';
                            //job_html += '<div class="jobTitle"><a href="' + CWS.apply_filter('result_job_url', joburl) + '"' + aria_replace + ' id="job-result' + i + '">' + CWS.apply_filter('result_job_title', job.title) + '</a></div>';
                            job_html += '<div class="jobTitle"><a href="' + CWS.apply_filter('result_job_url', joburl,job) + '"' + aria_replace + ' id="job-result' + i + '">' + CWS.apply_filter('result_job_title', job.title, job) + '</a>'+CWS.apply_filter('result_after_job_title','',job)+'</div>';

                        }
                        else if (cols[j] === 'title_category') {
                            job_html += '<div class="jobTitle"><a title="'+ job.title +', '+ job.primary_category +'" href="' + CWS.apply_filter('result_job_url', joburl, job) + '"' + aria_replace + ' id="job-result' + i + '">' + CWS.apply_filter('result_job_title', job.title, job) + '</a></div><div class="jobCategory">' + job.primary_category + '</div>';
                        }
                        else if (cols[j] === 'city_state') {
                            var location_formatted = job.primary_city + (job.primary_state ? ', ' + job.primary_state : (job.primary_country ? ', ' + job.primary_country : ''));
                            aria_message += '; Located in ' + aria_formatted_state;

                            job_html += location_formatted;
                            job_html += secondary_locations;
                        }
                        else if (cols[j] === 'address_city_state') {
                            var address = job.primary_address ? '<div class="job-address">' + job.primary_address + '</div>' : '';
                            var location_formatted = address + '<div class="job-locale">' + job.primary_city + (job.primary_state ? ', ' + job.primary_state : (job.primary_country ? ', ' + job.primary_country : '')) + '</div>';

                            job_html += location_formatted;
                            job_html += secondary_locations;
                        }
                        else if (cols[j] === 'city_state_locationtype') {
                            var location_formatted = job.primary_city + (job.primary_state ? ', ' + job.primary_state : (job.primary_country ? ', ' + job.primary_country : ''));
                            aria_message += '; Located in ' + aria_formatted_state;

                            job_html += '<div class="parent location">' + location_formatted + '</div>';
                            job_html += secondary_locations;
                            if (job.location_type) {
                                job_html += '<div class="child locationtype">' + job.location_type + '</div>';
                            }
                        }
                        else if (cols[j] === 'city_state_or_locationtype') {
                            if (job.location_type) {
                                // Statewide jobs should show the state, and the full state name not the initials
                                if (job.location_type === 'Statewide' && job.primary_state && CWS.states && job.primary_state in CWS.states) {
                                    job_html += '<div class="parent location">' + CWS.states[job.primary_state] + '</div>';
                                }
                                job_html += '<div class="child locationtype">' + job.location_type + '</div>';
                                aria_message += ', Location Type: ' + job.location_type;
                            }
                            else {
                                var location_formatted = job.primary_city + (job.primary_state ? ', ' + job.primary_state : (job.primary_country ? ', ' + job.primary_country : ''));
                                aria_message += '; Located in ' + aria_formatted_state;

                                job_html += '<div class="parent location">' + location_formatted + '</div>';
                                job_html += secondary_locations;
                            }
                        }
                        else if (cols[j] === 'city_state_country') {
                            var location_formatted = job.primary_city + (job.primary_state ? ', ' + job.primary_state : '') + (job.primary_country ? ', ' + job.primary_country : '');
                            aria_message += '; Located in ' + aria_formatted_state;

                            job_html += location_formatted;
                            job_html += secondary_locations;
                        }
                        else if (cols[j] === 'address_city_state_country') {
                            var address = job.primary_address ? '<div class="job-address">' + job.primary_address + '</div>' : '';
                            var location_formatted = address + '<div class="job-locale">' + job.primary_city + (job.primary_state ? ', ' + job.primary_state : '') + (job.primary_country ? ', ' + job.primary_country : '') + '</div>';
                            aria_message += '; Located in ' + aria_formatted_state;

                            job_html += location_formatted;
                            job_html += secondary_locations;
                        }
                        else if (cols[j] === 'city_state_country_locationtype') {
                            var location_formatted = job.primary_city + (job.primary_state ? ', ' + job.primary_state : '') + (job.primary_country ? ', ' + job.primary_country : '');
                            aria_message += '; Located in ' + location_formatted;

                            job_html += '<div class="parent location">' + location_formatted + '</div>';
                            job_html += secondary_locations;
                            if (job.location_type) {
                                job_html += '<div class="child locationtype">' + job.location_type + '</div>';
                            }
                        }
                        else if (cols[j] === 'city_state_country_or_locationtype') {
                            if (job.location_type) {
                                if (job.location_type === 'Statewide' && job.primary_state && CWS.states && job.primary_state in CWS.states) {
                                    job_html += '<div class="parent location">' + CWS.states[job.primary_state] + '</div>';
                                }
                                job_html += '<div class="child locationtype">' + job.location_type + '</div>';
                            }
                            else {
                                job_html += '<div class="parent location">' + job.primary_city + (job.primary_state ? ', ' + job.primary_state : '') + (job.primary_country ? ', ' + job.primary_country : '') + '</div>';
                                job_html += secondary_locations;
                            }
                        }
                        else if (cols[j] === 'addtnl_categories') {
                            for (cat in job[cols[j]]) {
                                job_html += '<div class="addtnl_category">' + job[cols[j]][cat] + '</div>';
                            }
                        }
                        else if (cols[j] === 'open_date') {
                            aria_message += '; Posted on ' + aria_formatted_date;
                            job_html += formatted;
                        }
                        else if (cols[j] === 'job_apply_button') {
                            //add a filter for changing any custom URLs
                            job = CWS.apply_filter("change_apply_url", job);
                            job_html += '<div class="avia-button avia-color-theme-color job-list-apply-btn"><a target="_blank" href="' + CWS.apply_filter('apply_link', job[options.job_apply_link], job) + '">'+ CWS.apply_filter('apply_link_text', options.job_apply_button_text, job) + '</a></div>';
                        }
                        else if (cols[j] === 'erp_eligible') {
                            //add a filter for changing any custom URLs
                            if(job.erp_eligible =='true')
                                job_html += CWS.apply_filter('results_erp_eligible', 'Yes', job);
                            else
                                job_html += CWS.apply_filter('results_erp_eligible', 'No', job);
                        }
                        else if (cols[j] === 'job_description') {
                            var detailedJD = "";
                            //Strippring HTML here
                            detailedJD = job.description.replace(/<[^>]*>?/gm, '');
                            //Removing extra spaces and multiple line breaks
                            detailedJD = detailedJD.replace(/\n\s*\n/g, '\n');
                            detailedJD = detailedJD.split(/\s+/);
                            var newJD = "";
                            var limit_job_desc = 50;
                            if(options.jobs_description_limit_words > 0) {
                                limit_job_desc = options.jobs_description_limit_words;
                            }
                            if(detailedJD.length > limit_job_desc) {
                                detailedJD = (detailedJD).slice(0,limit_job_desc);
                                detailedJD.push("...");
                            }
                            for(var jd = 0; jd < detailedJD.length ; jd++) {
                                newJD = newJD.concat(" ",detailedJD[jd]);
                            }
                            job_html += '<div class="jobSummary">' + CWS.apply_filter('result_job_summary', newJD, job) + '</div>';
                        }
                        else {
                            aria_message += '; ' + col_labels[j] + ': ' + job[cols[j]];
                            job_html += job[cols[j]];
                        }

                        job_html = CWS.apply_filter('after_job_column_' + cols[j], job_html, job);

                        if(j === (len2 - 1)){
                            // Might need to append something like an apply button
                            job_html = CWS.apply_filter('after_last_column', job_html, job);
                        }

                        job_html += '<span class="ripple-container"></span></div>';
                        job_html += '<span class="job-arrow-btn av_font_icon avia_animate_when_visible av-icon-style-  av-no-color avia-icon-pos-right  avia_start_animation avia_start_delayed_animation">';
                        job_html += '<a href="' + CWS.apply_filter('result_job_url', joburl, job) + '" class="av-icon-char" tabindex="-1" aria-hidden="true" data-av_icon="" data-av_iconfont="entypo-fontello"></a></span>';
                    }

                    job_html = CWS.apply_filter('after_job_columns', job_html, job);
                    job_html += '</div></div>';

                    if (cws_opts && cws_opts.accessible) {
                        // Confused by this? We needed to go through and make a label that would be read in order by a screen reader.
                        // Go through each column, add it to the label, then replace the {aria_title} on the job link
                        job_html = job_html.replace('{aria_title}', 'aria-label="' + aria_message + '"');
                    }

                    html += job_html;
                }
            }
            else {
                html = '<div id="no_results_found" tabindex="0">';
                if (options.pollinator_noresults) {
                    html += options.pollinator_noresults_text.replace('{{pollinator_link}}', '<a href="' + poll_url + '" ' + (cws_opts && cws_opts.accessible ? 'target="_blank"' : 'onclick="CWS.show_pollinator_lightbox(this); return false;"') + ' class="findly-connect-lightbox">' + options.pollinator_noresults_link + '</a>');
                }
                else {
                    // Generic message. The jQuery renders html encoded chars.
                    html += '<div class="error" >' + $('<div/>').html(options.results_none).text() + '</div>';
                }
                html += '</div>';

                focus_el = '#no_results_found';
            }
        }
        else {
            html = '<div id="results_error" class="error">' + options.results_error + '</div>';
            focus_el = '#results_error';
            CWS.log(data);
        }
        $('#widget-jobsearch-results-list').html(html);
        if (cws_opts) {
            if (focus_on_first_result) {
                setTimeout(function () {
                    // What's happening here? Focusing on a link overrides an aria-live element. Focus first, then use a live message.
                    $(focus_el).focus();
                    focus_on_first_result = false;
                    if (total_jobs > 0 && cws_opts.accessible) {
                        CWS.aria_live('Displaying page ' + (page + 1) + ' of ' + total_jobs + ' jobs.', true);
                    }
                }, 500);
            }
            /*
             setTimeout(function(){
             if(total_jobs == 0){
             CWS.aria_live('No jobs found.', true);
             }
             else{
             CWS.aria_live('Displaying page ' + (page + 1) + ' of ' + total_jobs + ' jobs.', true);
             }
             }, 750);
             if(focus_on_first_result) {
             setTimeout(function () {
             $(focus_el).focus();
             focus_on_first_result = false;
             }, 2000);
             }
             */
        }

        if(!options.hide_pagination_job_list) {
            display_pages();
        }
    };

    function sanitizeParamValue(value){
        if (typeof value !== 'string') {
            return '';
        }

        let sanitizedValue = value.replace(/['"]/g, ''); // Remove single and double quotes
        if (typeof DOMPurify !== 'undefined') {
            sanitizedValue = DOMPurify.sanitize(sanitizedValue) || '';
        }
        const blockedWords = [
            'script', 'onerror', 'onfocus', 'onblur', 'window.location', 'onclick', 'onmouseover', 'onpointerleave', 'onpointermove', 'onmouseleave', 'onmousemove', 'confirm'
        ];
        const regex = new RegExp('\\b(' + blockedWords.join('|') + ')\\b', 'gi');
        sanitizedValue = sanitizedValue.replace(regex, '');            

        return sanitizedValue;
    }
    // Moves focus to the next focusable element after the given element.
    // It searches for visible, enabled elements (links, buttons, inputs, etc.)
    // in the DOM after the provided element, and focuses the first one found.
    function focusNextFocusable(focuselement){
        if (!focuselement || !focuselement.length) {
            return;
        }
        var selectors = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
        var nextFocusable = focuselement.nextAll().find(selectors).filter(':visible').first();
        if(!nextFocusable.length){
            nextFocusable = focuselement.nextAll(selectors).filter(':visible').first();
        }
        if(nextFocusable.length){
            if(nextFocusable.closest('#widget-jobsearch-results-list').length > 0){
                focus_on_first_result = true;
            }else{
                nextFocusable.focus();
            }
        }
    }

    var refresh_tags = function(criteria, e){
        var filters = _.pick(criteria, 'SearchText', 'multiCategory', 'facet');
        if(criteria.latitude && criteria.longitude){
            // Deleted from the criteria as the location string isn't used, we need to add it back
            filters['Location'] = $('#cws_jobsearch_location').val();
        }

        filters = CWS.apply_filter('search_tag_filters', filters);

        var tags = '';
        var targetValue = '';
        for(var param in filters) {
            if(param !== 'facet') {
                var unspacevalue = encodeURI(filters[param]); //This will allow &+() characters in the tags without encoding.
                var value = unspacevalue.replaceAll("%20"," ");
                var sanitized_value = sanitizeParamValue(value);
                if (value != '') {
                    tags += '<li><button aria-label="' + sanitized_value + ' ' + CWS._('Remove from search') + '" class="search-tag" data-tag-param="' + encodeURIComponent(param) + '" data-tag-facet="" data-tag-value="' + sanitized_value + '">' + decodeURI(sanitized_value) + ' <span class="close-mark" aria-hidden="true">&times;</span></button></li>';
                }
            }
            else{
                for(var i = 0, len = filters[param].length; i < len; i++) {
                    var delimited = filters[param][i].split(':');
                    var facet = encodeURIComponent(delimited[0]);
                    var value = delimited[1].replaceAll("~",",");
                    value = CWS.apply_filter('modify_facet_value', value, delimited);
                    var sanitized_value = sanitizeParamValue(value);
                    if (value != '') {
                        tags += '<li><button aria-label="' + sanitized_value + ' ' + CWS._('Remove from search') + '"  class="search-tag" data-tag-param="' + encodeURIComponent(param) + '" data-tag-facet="' + facet + '" data-tag-value="' + sanitized_value + '">' + decodeURIComponent(sanitized_value) + ' <span class="close-mark" aria-hidden="true">&times;</span></button></li>';
                    }
                }
            }
        }

        if(tags !== ''){
            tags += '<li><a href="#" class="clear-tags" role="button">' + CWS._('Clear all') + '</a></li>';
        }

        if(e && e.target !== undefined){
             if($(e.target).hasClass('close-mark') === true){
                targetValue = $(e.target).parent().parent().next().text();
            }
            else{
                targetValue = $(e.target).parent().next().text();
            }
        }
        
        $('#search-filters').html(tags);

        if (e && $(e.target).closest('input,select,.select2-container').length === 0) {
            if(tags){
                $('#search-filters').find("*:contains('" + targetValue + "')").focus();
            }            
            else{
                focusNextFocusable($('#search-filters'));
            }
        }

    };

    var remove_criteria = function(param, facet, value, e){
        var all = param === null;
        var selector = 'input[data-param="' + param + '"],select[data-param="' + param + '"]';
        if(all){
            // If this function is called without params, assume that all criteria must be removed
            selector = 'input[data-param],select[data-param]';
        }

        $(selector).each(function(){
            if(all){
                if(!$(this).is('input[type="checkbox"]')) {
                    $(this).val('');
                    if ($(this).is('select')) {
                        $(this).trigger('change.select2');
                    }
                } else {
                    $(this).prop('checked', false);
                    $(this).removeAttr('checked');
                }
            }
            else {
                var input_facet = $(this).data('facet');
                if (typeof input_facet === 'undefined' || input_facet == facet) {
                    if(!$(this).is('input[type="checkbox"]')) {
                        $(this).val('').trigger('change.select2');
                    }else if($(this).prop("checked") == true) {
                        $(this).prop('checked', false);
                        $(this).removeAttr('checked');
                    }
                }


                if (param === 'Location') {
                    $('#cws_jobsearch_latitude,#cws_jobsearch_longitude,#cws_jobsearch_state,#cws_jobsearch_country').val('');
                }
            }
        });

        gather_criteria(e);
    };

    var last_clicked_search_field = function() {
        $("input[type=checkbox]").click(function(){
            var atleast_one_is_checked = false;
            $(".last-clicked-search-field").removeClass("last-clicked-search-field");
            $(this).closest('.search-checkbox-container').find('input[type=checkbox]').each(function () {
                if($(this).is(":checked")) {
                    atleast_one_is_checked = true;
                }
            })
            if(atleast_one_is_checked) {
                $(this).closest('.search-checkbox-container').addClass('last-clicked-search-field');
            }
        });

        $("select").change( function() {
            $(".last-clicked-search-field").removeClass("last-clicked-search-field");
            if($(this).val() !== null){
                if($(this).val().length !== 0) {
                    $(this).closest('.search-control-container').addClass('last-clicked-search-field');
                }            
            }
        });

    };

    var clear_search_field = function() {
        $('.search-control-container').on('click', '.search_tag', function(e){
            var param = $(this).data('tag-param'),
                facet = $(this).data('tag-facet'),
                value = $(this).data('tag-value');
            remove_criteria(param, facet, value, e);
        });
    };

    var refresh_checkboxes = function(hashmap = false) {
        $('.search-checkbox-container').each(function() {
            if (hashmap) {
                var facet = $(this).data('facet');
                var is_last_clicked_checkbox_field = $(this).closest('.search-checkbox-container').hasClass('last-clicked-search-field');
                if (facet in hashmap && !is_last_clicked_checkbox_field) {
                    // Leave the selected option in there if they were linked to the search results page
                    $(this).find('input[type=checkbox]').each(function () {
                        var matched = false;
                        var checkbox_label = $(this).attr('value');
                        var job_count_checkbox = hashmap[facet][checkbox_label];
                        if(job_count_checkbox === 'undefined' || job_count_checkbox === undefined) {
                            job_count_checkbox = 0;
                        }
                        if($(this).closest('.search-checkbox-container').hasClass('custom')) {                            
                            matched = custom_option_matches_facet(checkbox_label, hashmap[facet]);
                            if(options.inc_facetcount) {
                                job_count_checkbox = custom_options_count(checkbox_label, hashmap[facet]);
                            }
                        }
                        if(options.inc_facetcount) {
                            $("label[for='" + $(this).attr("id") + "']").text(checkbox_label + " " + "(" + job_count_checkbox + ")");
                        } else {
                            $("label[for='" + $(this).attr("id") + "']").text(checkbox_label);
                        }
                        if (hashmap[facet].hasOwnProperty(checkbox_label) || $(this).is(":checked") || matched) {
                            $(this).parent().show();
                        } else {
                            $(this).parent().hide();
                        }
                    });
                } else if(facet in hashmap && is_last_clicked_checkbox_field) {
                    $(this).find('input[type=checkbox]').each(function () {
                        var matched = false;
                        var checkbox_label = $(this).attr('value');
                        $("label[for='" + $(this).attr("id") + "']").text(checkbox_label);
                        if($(this).closest('.search-checkbox-container').hasClass('custom')) {
                            matched = custom_option_matches_facet(checkbox_label, hashmap[facet]);
                        }
                        if (hashmap[facet].hasOwnProperty(checkbox_label) || $(this).is(":checked") || matched) {
                            $(this).parent().show();
                        } else {
                            $(this).parent().hide();
                        }
                    });
                }
            } else {
                $(this).find('input[type=checkbox]').each(function () {
                    var checkbox_label = $(this).attr('value');
                    $(this).parent().show();
                    $("label[for='" + $(this).attr("id") + "']").text(checkbox_label);
                });
            }
        });
    }

    var refresh_current_dropdowns = function(facetlists){

        var current_filter = $('select.opendropdown');
        if(typeof facetlists === 'object'){
            var hashmap = {};
            for(var facet in facetlists){
                hashmap[facet] = {};
                for(var i = 0, len = facetlists[facet]['buckets'].length; i < len; i++){
                    var val = facetlists[facet]['buckets'][i]['key'];
                    var count = facetlists[facet]['buckets'][i]['doc_count'];

                    hashmap[facet][val] = count;
                }
            }

            var facet = current_filter.data('facet');
            var selectedValues  = current_filter.val();
            if(facet in hashmap){
                current_filter.children('option').each(function(){
                    //|| $.inArray($(this).attr('value'), selectedValues)
                    if(current_filter.closest('.search-control-container').hasClass('custom')) {
                        var matched = custom_option_matches_facet($(this).attr('value'), hashmap[facet]);
                        if(matched) {
                            $(this).removeAttr('disabled');
                            if(options.inc_facetcount) {
                                var total_count = custom_options_count($(this).attr('value'), hashmap[facet]);
                                this.text = this.text.replace(/\(\d+\)/, `(${total_count})`);
                            }
                        } else {
                            $(this).attr('disabled', true);
                        }
                    }
                    else if(hashmap[facet].hasOwnProperty($(this).attr('value'))){
                        $(this).removeAttr('disabled');
                        if(options.inc_facetcount) {
                            let key  = $(this).attr('value');
                            if (hashmap[facet].hasOwnProperty(key)) {
                                var updatedString = this.text.replace(/\(\d+\)/, `(${hashmap[facet][key]})`);
                                this.text = updatedString;
                            }
                        }
                    }
                    else{
                        $(this).attr('disabled',true);
                    }
                });
                
                if (cws_opts && !cws_opts.accessible && jQuery('body.l-body').length === 0){
                    CWS.build_single_select2(current_filter);
                    current_filter.select2('open');
                }
            }  
        }
        else{
            current_filter.children('option').removeAttr('disabled');
            $('select[data-facet]').removeClass("opendropdown")
            CWS.build_select2(current_filter);
            refresh_checkboxes();
        }

        /** Start Remove Loader to current dropdown */
        let loaderContainer = $('.search-control-container').find('.select2-container');
        loaderContainer.removeClass('loading');
        loaderContainer.find('.loading-spinner').remove();
        
    }

    var custom_option_matches_facet = function(optionValue, hashmapFacet) {
        if (!optionValue) return false;

        var options = optionValue.split('~');

        for (var i = 0; i < options.length; i++) {
            var part = options[i].trim().toLowerCase();

            for (var key in hashmapFacet) {
                if (hashmapFacet.hasOwnProperty(key)) {
                    if (key.toLowerCase() === part) {
                        return true;
                    }
                }
            }
        }
        return false;
    }

    var custom_options_count = function(optionValue, hashmapFacet) {
        var sum = 0;
        if (!optionValue) return sum;

        var parts = optionValue.split('~');

        for (var i = 0; i < parts.length; i++) {
            var part = parts[i].trim().toLowerCase();

            for (var key in hashmapFacet) {
                if (hashmapFacet.hasOwnProperty(key)) {
                    if (key.toLowerCase() === part) {
                        sum += parseInt(hashmapFacet[key]) || 0;
                    }
                }
            }
        }
        return sum;
    }

    var refresh_dropdowns = function(facetlists, callback_fn){
        var focusedBeforeRebuild = $(document.activeElement);
        var focusWasInSelect2 = focusedBeforeRebuild.closest('.select2-container').length > 0;
        var commuteControl = focusedBeforeRebuild.closest('.commute-control');
        if(typeof facetlists === 'object'){
            var hashmap = {};
            for(var facet in facetlists){
                hashmap[facet] = {};
                for(var i = 0, len = facetlists[facet]['buckets'].length; i < len; i++){
                    var val = facetlists[facet]['buckets'][i]['key'];
                    var count = facetlists[facet]['buckets'][i]['doc_count'];

                    hashmap[facet][val] = count;
                }
            }

            $('select[data-facet]').each(function(){
                var facet = $(this).data('facet'),
                is_multi = $(this).hasClass('multi');
                var is_last_clicked_select_field = $(this).closest('.search-control-container').hasClass('last-clicked-search-field');

                // for accessibility aria-label 
                var field_label = ''; 
                var clear_label = '';
                if($(this).parents('.search-control-container').children('.w-form-row-label').find('label').length>0){
                    field_label = $(this).parents('.search-control-container').children('.w-form-row-label').find('label').text();
                    clear_label = 'Remove '+ $(this).val() + ' from '+ field_label;
                }else{
                    clear_label = 'Remove '+ $(this).val();
                }

                
                if(!is_multi && $(this).val() && !is_last_clicked_select_field) {	
                    $(this).prop('disabled', 'disabled');
                    $('div.w-form-row-label').css("float","left");
                    $('.clear_selection_'+ $(this).attr('name')).remove();
                    $(this).before('<a class="clear_selection_'+ $(this).attr('name') +' search_tag" role="button" aria-label="'+ clear_label +'" data-tag-param="facet" data-tag-facet="'+ encodeURIComponent($(this).attr('data-facet')) +'" data-tag-value="'+ encodeURIComponent($(this).val()) +'">Clear</a>');
                    $('.clear_selection_'+ $(this).attr('name')).css({'float':'right','cursor':'pointer'});
                    $('.clear_selection_'+ $(this).attr('name')).attr('tabindex','0');
                } else if( !is_multi && $(this).val() ){
                    $('div.w-form-row-label').css("float","left");
                    $('.clear_selection_'+ $(this).attr('name')).remove();
                    $(this).before('<a class="clear_selection_'+ $(this).attr('name') +' search_tag" role="button" aria-label="'+ clear_label +'" data-tag-param="facet" data-tag-facet="'+ encodeURIComponent($(this).attr('data-facet')) +'" data-tag-value="'+ encodeURIComponent($(this).val()) +'">Clear</a>');
                    $('.clear_selection_'+ $(this).attr('name')).css({'float':'right','cursor':'pointer'});
                }else {
                    $(this).removeAttr('disabled');
                    $('.clear_selection_'+ $(this).attr('name')).remove();
                }

                //Remove the disabled attribute for the quick search widget select input.
                if(jQuery(this).parent('.search-control-container').parent().hasClass('quicksearch-field')){
                    $(this).prop('disabled', false);
                }

                //We need to check if Chaining Load Dropdown Filter(s) In Real Time is disabled.
                if(!cws_opts.chaining_load_realtime_filter){
                    if(facet in hashmap && !is_last_clicked_select_field){
                        // Leave the selected option in there if they were linked to the search results page
                        $(this).children('option').each(function(){
                            // For Custom dropdowns, disable the option only if none of the parts in the option value match the facet values.
                            if($(this).closest('.search-control-container').hasClass('custom')){
                                var matched = custom_option_matches_facet($(this).attr('value'), hashmap[facet]);
                                if(matched){
                                    $(this).removeAttr('disabled');
                                    if(options.inc_facetcount) {
                                        var total_count = custom_options_count($(this).attr('value'), hashmap[facet]);
                                        this.text = this.text.replace(/\(\d+\)/, `(${total_count})`);
                                    }
                                } else {
                                    if($(this).val() != ""){
                                        $(this).prop('disabled', 'disabled');
                                    }
                                }
                            }
                            else if(hashmap[facet].hasOwnProperty($(this).attr('value'))){
                                $(this).removeAttr('disabled');
                                //set the value of the new drop down here
                                //change to this.text and the corresponding value
                                for (var key in hashmap[facet]) {
                                    if (hashmap[facet].hasOwnProperty(key)) {
                                        // temp key to taken to retain the case sensitive string in key.
                                        var tempKey = key;
                                        if(tempKey.toLowerCase() === this.value.toLowerCase()) {
                                            if(options.inc_facetcount) {
                                                this.text = this.text.replace(/\(\d+\)/, `(${hashmap[facet][key]})`);
                                            }
                                        }
                                    }
                                }
                            }
                            else{
                                var is_already_selected = $(this).attr('data-select2-id');
                                if (typeof is_already_selected !== 'undefined' && is_already_selected !== false && is_multi) {
                                    if(options.inc_facetcount) {
                                        this.text = this.text.replace(/\(\d+\)/, `(0)`);
                                    }
                                } else {
                                    if($(this).val() != ""){
                                        $(this).prop('disabled', 'disabled');
                                    }
                                }

                            }

                            //Remove the disabled attribute for the quick search widget select options if 'chaining' is disabled.
                            if(jQuery(this).parent('.search-control-container').parent().hasClass('quicksearch-field') && !jQuery(this).parent('.search-control-container').parent('.quicksearch-field').hasClass('dynamic-criteria-field')){
                                $(this).children('option').prop('disabled', false);
                            }

                        });
                        if (cws_opts && !cws_opts.accessible && jQuery('body.l-body').length === 0){
                            CWS.build_select2();
                        }
                    }
                }

            });
            refresh_checkboxes(hashmap);
        }
        else{
            if(!cws_opts.chaining_load_realtime_filter){
                $('select[data-facet]').each(function(){
                    $(this).children('option').removeAttr('disabled');
                    CWS.build_select2();
                });
            }
            refresh_checkboxes();
        }
        // After CWS.build_select2() destroys and recreates all select2 widgets,
        // focus falls to <body>. If it was on a filter control, restore it to the
        // recently-applied filter so the user stays in context.
        if (focusWasInSelect2 && document.activeElement === document.body) {
            var $lastClicked = $('.last-clicked-search-field select.text_select');
            if(commuteControl.length) {
                commuteControl.find('.select2-container').find('.select2-selection').focus();
            }
            else if($lastClicked.length) {
                $lastClicked.next('.select2-container').find('.select2-selection').focus();
            }
        }
        // For the callback function - combobox accessibility 
        callback_fn();

    };

    var display_pages = function () {
        var num_pages = Math.ceil(total_jobs / options.limit);

        num_pages = CWS.apply_filter('search_results_last_page_number', num_pages, total_jobs, options.limit);

        // Try to have the current page in the middle if possible.
        var extra_pages = 0;
        var begin = page - Math.floor(options.max_pages / 2);
        var end = page + Math.floor(options.max_pages / 2);

        if (begin < 0) {
            extra_pages += Math.abs(0 - begin);
            end += extra_pages;
            begin = 0;
        }
        if (end > num_pages) {
            begin -= end - num_pages;
            end = num_pages;
        }
        if (begin < 0) {
            begin = 0;
        }
        var html = '<ul class="pagination-ul" >';

        if (num_pages > 1) {
           if (page > 0) {
                html += '<li class="pagination-li"><a href="#" onclick="CWS.jobs.goto_page(0, true); return false;" class="button inactive" role="button" aria-label="' + CWS._('Go to the first page of results.') + '">&lt;&lt;</a></li>';
                html += '<li class="pagination-li"><a href="#" onclick="CWS.jobs.prev_page(); return false;" class="button inactive" role="button" aria-label="' + CWS._('Go to the previous page of results.') + '">&lt;</a></li>';
            }

            for (var i = begin; i < end; i++) {
                
                html += '<li class="pagination-li">';
                html += '<a href="#" id="pagination'+(i+1)+'" onclick="CWS.jobs.goto_page(' + i + ', true); return false;" class="button ' + (page == i ? CWS.layout_builder.buttons.base() + ' ' + CWS.layout_builder.buttons.color('primary') : 'inactive') + ' style_raised" role="button" aria-label="' + CWS._('Page') + ' ' + (i + 1) + '" aria-current="false">' + (i + 1) + '</a>';
                html += '</li>';
            }

            if (page + 1 < num_pages) {
                html += '<li class="pagination-li"><a href="#" onclick="CWS.jobs.next_page(); return false;" class="button inactive" role="button" aria-label="' + CWS._('Go to the next page of results.') + '">&gt;</a> </li>';
                html += '<li class="pagination-li"> <a href="#" onclick="CWS.jobs.goto_page(' + (num_pages - 1) + ', true); return false;" class="button inactive"  role="button" aria-label="' + CWS._('Go to the last page of results.') + '">&gt;&gt;</a> </li>';
            }
            $('#widget-jobsearch-results-pages').removeAttr('aria-hidden');
        }else{
            $('#widget-jobsearch-results-pages').attr('aria-hidden', 'true');
        }
        html += '</ul>';
        $('#widget-jobsearch-results-pages').html(html);
        $('.pagination-li').children().not('.inactive').attr('aria-current', true);
    };

    var get_location = function (criteria, pollinator_data, querystring) {
        if (options.display_loading_bar && loader && typeof loader.start === 'function') {
            loader.start();
            $('.widget-jobsearch-results').find('.search-results-table').attr("aria-busy","true");
        }

        // It should be safe to show the geolocation icon again assuming the location changed by user input
        $('.location-wrapper.with_geo .geolocation-icon').show().parent().addClass('with_geo');

        var location_input = criteria.Location;
        delete criteria.Location;

        // Retrieve the full location (city, state, country) from IP2Location
        var iplocation = $('#cws_jobsearch_iplocation').val(),
            country = $('#cws_jobsearch_country'),
            state = $('#cws_jobsearch_state');


        if (autocomplete) {
            var place = autocomplete.getPlace();

            if (place && place.geometry) {
                place = CWS.apply_filter('search_results_place_returned', place);

                // Things changed, let's start fresh
                if (criteria.facet) {
                    criteria.facet = remove_facets(criteria.facet, ['primary_state', 'primary_country']);
                }
                delete criteria['latitude'];
                delete criteria['longitude'];
                delete querystring['latitude'];
                delete querystring['longitude'];
                delete querystring['state'];
                delete querystring['country'];

                $('#cws_jobsearch_latitude').val('');
                $('#cws_jobsearch_longitude').val('');
                $('#cws-search-sortfield').val('');
                $('#cws-search-direction').val('');
                country.val('');
                state.val('');

                page = 0; // go back to the first page, no matter what kind of location we use
                $('#cws-search-page').val('');
                delete criteria['offset'];
                delete querystring['pg'];

                // We want to use primary_country and primary_state if they're
                if (place.address_components[0]['types'][0] === 'country') {
                    country.val(place.address_components[0]['short_name']);
                    if (typeof criteria['facet'] != 'object') {
                        criteria['facet'] = [];
                    }
                    //criteria['facet'].push('primary_country:' + place.address_components[0]['short_name']);
                    criteria['countryStateCity'] = place.address_components[0]['short_name'];
                    querystring['country'] = place.address_components[0]['short_name'];

                    $('#cws_jobsearch__proximity').attr('disabled', 'disabled');
                }
                else if (place.address_components[0]['types'][0] === 'administrative_area_level_1' && place.address_components[1]['short_name'] in statewide_whitelist) {
                    state.val(place.address_components[0]['short_name']);
                    if (typeof criteria['facet'] != 'object') {
                        criteria['facet'] = [];
                    }
                    querystring['state'] = place.address_components[0]['short_name'];

                    if (place.address_components.length > 1 &&  place.address_components[1]['types'][0] === 'country'){
                        country.val(place.address_components[1]['short_name']);
                        querystring['country'] = place.address_components[1]['short_name'];
                        criteria['countryStateCity'] = place.address_components[1]['short_name'] + ',' + place.address_components[0]['short_name'];
                    }
                    else{
                        criteria['facet'].push('primary_state:' + place.address_components[0]['short_name']);
                    }
                    $('#cws_jobsearch__proximity').attr('disabled', 'disabled');

                }

                // Back to regular radius search
                else {
                    criteria['latitude'] = place.geometry.location.lat();
                    criteria['longitude'] = place.geometry.location.lng();
                    querystring['latitude'] = place.geometry.location.lat();
                    querystring['longitude'] = place.geometry.location.lng();

                    $('#cws_jobsearch_latitude').val(criteria['latitude']);
                    $('#cws_jobsearch_longitude').val(criteria['longitude']);
                    $('#cws_jobsearch__proximity').removeAttr('disabled');

                    delete criteria['sortfield'];
                    delete criteria['sortorder'];
                    delete querystring['sort'];
                    delete querystring['dir'];

                    sortfield = ''; // need to sort by proximity
                }

                // Location Type
                if ($('#cws_jobsearch_nationwide_country').length > 0) {
                    // We're going to set the nationwideCountries and statewideStates params if they're available from Google
                    var state_country = get_state_country(place.address_components);

                    if (state_country.country) {
                        criteria['nationwideCountries'] = state_country.country;
                        querystring['nationwide'] = state_country.country;
                        $('#cws_jobsearch_nationwide_country').val(state_country.country);
                    }
                    if (state_country.state) {
                        criteria['statewideStates'] = state_country.state;
                        querystring['statewide'] = state_country.state;
                        $('#cws_jobsearch_nationwide_state').val(state_country.state);
                    }

                }

                last_place = place;

                querystring = CWS.apply_filter('search_results_querystring', querystring);
                History.replaceState(null, document.title, CWS.build_querystring(querystring));

                // The error class is applied for invalid locations, see else-statement below
                $('#cws_jobsearch_location,#cws_jobsearch_commute').attr('aria-invalid', 'false').parent().removeClass('error');
                $('#cws_jobsearch_commute').val($('#cws_jobsearch_location').val());
                $('.location-wrapper .error-msg').remove();

                if (place.address_components && place.address_components[0]) {
                    // General location... tries to get state. Will likely need to come back if empty for country.
                    pollinator_data['gl'] = place.address_components[0].short_name;
                }
            }

            else {
                // Check to see if the user has entered a location that is different than the
                // location brought over from IP2Location - if not, lat/lon can stay, otherwise
                // reset to -1 since we dont have a valid location.
                if (iplocation != location_input && !country.val() && !state.val()) {
                    // Not a real place? This needs a better solution. A much better solution.
                    criteria['latitude'] = '-1';
                    criteria['longitude'] = '-1';
                    $('#cws_jobsearch_latitude').val('-1');
                    $('#cws_jobsearch_longitude').val('-1');

                    if (country) {
                        country.val('');
                        state.val('');
                    }
                    var $locfield = $('#cws_jobsearch_location,#cws_jobsearch_commute');
                    $locfield.attr('aria-invalid', 'true')
                        .parent()
                        .addClass('error');
                    if ($('#loc-error').length == 0) {
                        $('#cws_jobsearch_location').parent().append('<div class="error-msg" id="loc-error">'+options.location_invalid_visible+'</div>');
                    }

                    $locfield.attr('aria-describedby', 'loc-error');
                    CWS.add_filter('search_results_invalid_location', null);
                }

            }
        }
        else {

            // Check to see if the user has entered a location that is different than the
            // location brought over from IP2Location - if not, lat/lon can stay, otherwise
            // reset to -1 since we dont have a valid location.
            if (iplocation != location_input && !country.val() && !state.val()) {
                // Not a real place? This needs a better solution. A much better solution.
                criteria['latitude'] = '-1';
                criteria['longitude'] = '-1';
                $('#cws_jobsearch_latitude').val('-1');
                $('#cws_jobsearch_longitude').val('-1');
                var $locfield = $('#cws_jobsearch_location,#cws_jobsearch_commute');
                $locfield.attr('aria-invalid', 'true')
                    .parent()
                    .addClass('error');
                if ($('#loc-error').length == 0) {
                    $('#cws_jobsearch_location').parent().append('<div class="error-msg" id="loc-error">'+options.location_invalid_visible+'</div>');
                }
                $locfield.attr('aria-describedby', 'loc-error');
                CWS.add_filter('search_results_invalid_location', null);
            }
        }
        search_jobs(criteria, pollinator_data);
    };

    var refresh_column_sort = function () {
        $('.col-controls .col-control,.col-controls').hide();
        $('.search-columns .flex_column').each(function () {
            var columnTitle = sanitizeParamValue($(this).text());
            if (sortfield == $(this).data('param')) {
                var sortedOrder = sanitizeParamValue(sortorder);
                $(this).closest('[id*="colhead"]').attr('aria-sort', sortedOrder);
                $(this).addClass('active').find('.col-controls,.col-control.del,.col-control.' + sortorder).show();
                if(!$(this).hasClass('unsortable')){
                    var columnAriaLabel = ''; 
                    if(sortedOrder == 'ascending'){
                        columnAriaLabel = columnTitle +', sortable column, sorted ascending, activate to sort column descending';
                    }else{
                        columnAriaLabel = columnTitle +', sortable column, sorted descending, activate to sort column ascending';
                    }
                    $(this).attr('aria-label', columnAriaLabel);
                }
            }
            else {
                $(this).removeClass('active');
                if(!$(this).hasClass('unsortable')){
                    $(this).attr('aria-label', columnTitle + ', sortable column, not sorted, activate to sort column ascending');
                }
                $(this).closest('[id*="colhead"]').removeAttr('aria-sort');
            }

            // Google API - Make open_date column unclickable, if the jobs are already sorted in open_date order
            if(cws_opts.api.includes('google') && $(this).data('param') == 'open_date'){
                if (sortfield == 'open_date') {
                    $(this).css('pointer-events','none');
                } else {
                    $(this).css('pointer-events','auto');
                }
            }
        });

        if(cws_opts.is_tablearrows_visibility_enabled === 'true'){
            $('.col-controls .col-control,.col-controls').show();
            $('.search-columns .flex_column:not(.active)').find('.col-controls').find('.col-control').removeClass('sorted');
            $('.search-columns .unsortable').find('.col-controls').css('display','none');
            $('.widget-jobsearch-results-list').find('.search-columns').find('.col-controls').css('display','none');
        }
    };

    var get_state_country = CWS.get_state_country;

    var seo_url_text = CWS.seo_url;

    var remove_facets = function (facets, vals) {
        var new_facets = [];
        for (var i = 0, len = facets.length; i < len; i++) {
            var matches = false;
            for (j = 0, jlen = vals.length; j < jlen; j++) {
                if (facets[i].indexOf(vals[j]) > -1) {
                    matches = true;
                }
            }

            if (!matches) {
                new_facets.push(facets[i]);
            }
        }
        return new_facets;
    };

    var save_last_search = function(criteria){
        // What we're saving is a little long, so we're going to use LocalStorage instead of cookies
        if(CWS.storageAvailable('localStorage')) {
            var str = '';

            if (criteria.SearchText) {
                str = criteria.SearchText + ' jobs';
            }
            else if (criteria.primary_category) {
                str = criteria.primary_category + ' jobs';
            }
            else if (criteria.multiCategory) {
                str = criteria.multiCategory.join(', ') + ' jobs';
            }
            else {
                str = 'Jobs';
            }

            str = str.replace('~', ' '); // just in case, we're using that to delimit
            str = str.replace('|', ' '); // just in case, we're using that to delimit
            str = CWS.apply_filter('last_search_str_jobs', str);
            var loc = '';

            if (criteria.latitude && last_place && last_place.vicinity) {
                loc = ' near ' + last_place.vicinity;
            }
            else if (criteria.stateCity) {
                if(typeof criteria.stateCity === 'object'){
                    loc = ' in ' + criteria.stateCity.join(', ');
                }
                else {
                    loc = ' in ' + criteria.stateCity.split(', ')[1];
                }
            }
            else if (criteria.countryStateCity) {
                if(typeof criteria.countryStateCity === 'object'){
                    loc = ' in ' + criteria.countryStateCity.join(', ');
                }
                else {
                    loc = ' in ' + criteria.countryStateCity.split(', ')[2];
                }
            }

            loc = CWS.apply_filter('last_search_str_location', loc);
            str = str + loc;

            // No sense in storing an empty search
            if (str !== 'Jobs') {
                var current = localStorage.getItem('last_searches');
                var full_str = str + '~' + window.location.pathname + window.location.search;

                if (current == null) {
                    localStorage.setItem('last_searches', full_str);
                }
                else {
                    current = current.split('|');

                    // I'm going to make it so that if the STRING matches, it doesn't matter what the page path is,
                    // the page path will update.
                    var existing_index = _.findIndex(current, function (src) {
                        return src.split('~')[0] == str;
                    });

                    // Remove the existing entry with the same string
                    if (existing_index !== -1) {
                        current.splice(existing_index, 1);
                    }

                    if (current.length == 10) {
                        current.shift();
                    }
                    current.push(full_str);


                    localStorage.setItem('last_searches', current.join('|'));
                }
            }
        }
    };

    var string_to_css_class = function(str){
        if ( !str ) {
            return '';
        }
        str = str.toLowerCase();
        str = str.replace(/[^a-z0-9]/g, ' ');
        str = str.replace(/\s/g, '-');
        str = str.replace(/-+/g, '-');
        return str;
    };

    // Google API - when the selected filters are cleared, the next search should be sorted by default sort order
    var set_default_sort = function(){
        if(cws_opts.api.includes('google')){
            $('#cws-search-sortfield').val('');
            $('#cws-search-direction').val('');
            sortfield = default_sortfield;
            sortorder = default_sortorder;
        }
    };

    // Public variables and functions
    return {
        marker_events_set: false,
        set_options: function (opts) {
            // Underscore makes sure we don't lose our default options
            options = _.defaults(opts, options);
        },
        get_option: function(opt){
            return options[opt];
        },
        set_api: function (url) {
            api_url = url;
        },
        set_columns: function (cols, col_spans, col_labels) {
            columns = cols;
            column_spans = col_spans;
            column_labels = col_labels;
            include_country = cols.indexOf('city_state_country') !== -1;
        },
        set_autocomplete: function (titles) {
            auto_titles = titles;
        },
        clear_field: function (el) {
            $(el).siblings('input,select').val('');
            if($(el).parent().is('.location-wrapper')) {
                $(el).parent().siblings('#cws_jobsearch_latitude, #cws_jobsearch_longitude, #cws_jobsearch_state, #cws_jobsearch_country').val('');
            }
            CWS.jobs.goto_page(0);
            CWS.apply_filter('clear_field_selection', el);
        },
        clear_all: function(el){
            event.preventDefault();
            var widget_jobsearch_full_horizontal = widgetDiv;
            var formFieldEmpty = true;
            $('.'+widget_jobsearch_full_horizontal).find('input[type!="hidden"], select[type!="hidden"]').each(function () {
                if($(this).val()) {
                    if($(this).val().length > 0 && $(this).attr('id') !== 'cws_jobsearch__proximity'){
                        formFieldEmpty = false;
                    }
                }
                if($(this).hasClass('select2-hidden-accessible')) {
                    var select_placeholder = $(this).attr('placeholder');
                    if (typeof select_placeholder !== 'undefined' && select_placeholder !== false) {
                        $(this).select2('destroy').val("").select2({'placeholder':select_placeholder});
                    } else {
                        $(this).select2('destroy').val("").select2();
                    }
                }
                if($(this).is(':checkbox') && $(this).attr('id') !== 'job-live-search') {
                    // On clear_all we uncheck all checkboxes except the live search checkbox
                    $(this).prop("checked", false);
                }
                else if ($(this).attr('id') !== 'cws_jobsearch__proximity') {
                    $(this).val('');
                }
                else if ($(this).attr('id') === 'cws_jobsearch__proximity') {
                    var defaultOption = $(this).find('option[selected]');
                    var radius = defaultOption.val() ? defaultOption.val() : $(this).val();
                    $(this).val(radius);
                }
            });
            $('#cws_jobsearch_latitude').val('');
            $('#cws_jobsearch_longitude').val('');
            set_default_sort();
            if(!formFieldEmpty) {
                CWS.jobs.goto_page(0);
                CWS.apply_filter('clear_all_field_selection', el);
            }
            if(options.dynamic_criteria) {
                gather_criteria(el);
            }

            if(options.dynamic_criteria){
                $(".search-control-container").removeClass("last-clicked-search-field");
                $('select.opendropdown').removeClass("opendropdown");
            }
            singleselect_accessibilty();
            
            /* Move focus to first input/focusable element in filter section */
            const firstFocusable = $('.'+widget_jobsearch_full_horizontal)
                .find('input:not([type=hidden]), select, textarea, button')
                .filter(':visible:enabled')
                .first();
            if (firstFocusable.length) {
                if (firstFocusable.hasClass('select2-hidden-accessible') && typeof firstFocusable.select2 === 'function') {
                    $('.select2-selection__rendered').removeAttr('tabindex');
                    firstFocusable.select2('focus');
                } else {
                    firstFocusable.focus();
                }
            }
        },
        init_loc_autocomplete: function () {
            var location_field = document.getElementById('cws_jobsearch_location');
            $(location_field).each(function() {
                if ($(this).is('input')) {
                    var location_field = $(this).get(0);

                    /******* THIS SECTION SELECTS THE FIRST LOCATION ON ENTER **********/
                        // jQuery doesn't return an event object, so we need to check for IE-compatibility
                    var _addEventListener = (location_field.addEventListener) ? location_field.addEventListener : location_field.attachEvent;

                    function addEventListenerWrapper(type, listener) {
                        // Simulate a 'down arrow' keypress on hitting 'return' when no pac suggestion is selected,
                        // and then trigger the original listener.
                        if (type == "keydown") {
                            var orig_listener = listener;
                            listener = function (event) {
                                var suggestion_selected = $(".pac-item-selected").length > 0;
                                if (event.which == 13 && !suggestion_selected) {
                                    var simulated_downarrow = $.Event("keydown", {keyCode: 40, which: 40});
                                    orig_listener.apply(location_field, [simulated_downarrow]);

                                    if ($(".pac-container").css('display') != 'none') {
                                        event.preventDefault();
                                    }
                                    else {
                                        page = 0;
                                    }
                                }

                                orig_listener.apply(location_field, [event]);
                            };
                        }

                        // add the modified listener
                        _addEventListener.apply(location_field, [type, listener]);
                    }

                    // More IE-checks
                    if (location_field.addEventListener) {
                        location_field.addEventListener = addEventListenerWrapper;
                    }
                    else if (location_field.attachEvent) {
                        location_field.attachEvent = addEventListenerWrapper;
                    }
                    /********************* END HACK *************************/

                    /*autocomplete = new google.maps.places.Autocomplete(location_field,
                    {
                        'types': ['geocode']
                    });

                    autocomplete.setTypes(['geocode']);*/


                    var location_type = cws_opts.google_place_search_type; // For getting Default Search type

                    if (location_type == 'all' || location_type == '') { //comparing with null if Search type is not set/configured.
                        var place_set_type = {
                            'types': []  // for all types initialize to null
                        };
                        location_type = '';
                    } else {
                        var place_set_type = {
                            'types': [location_type]
                        };
                    }

                    autocomplete = new google.maps.places.Autocomplete(location_field, place_set_type);

                    autocomplete.setTypes([location_type]);

                    // I've placed this in the options object so it can be overwritten, if needed, in the <head> tag
                    if (cws_opts.google_place_fields) {
                        autocomplete.setFields(cws_opts.google_place_fields);
                    }

                    google.maps.event.addListener(autocomplete, 'place_changed', function (e) {
                        gather_criteria(null, true);
                    });

                    $(location_field).keydown(function (e) {
                        if (e.keyCode == 13 && $(".pac-container").css('display') != 'none') {
                            setTimeout(function () {
                                CWS.aria_live($(location_field).val() + ' selected.', true);
                            }, 500);
                        }
                    });
                    $(location_field).on('focus', function (e) {
                        CWS.aria_live('Type a location, and use the down arrow to select a suggestion.', true);
                    });
                    $(location_field).keyup(function (e) {
                        var $ = jQuery;
                        // Up, down arrows
                        if (e.keyCode == 40 || e.keyCode == 38) {
                            if ($('.pac-container').css('display') == 'none' || $('.pac-item').length == 0) {
                                CWS.aria_live('No locations match your input.', true);
                            }
                            else if ($('.pac-item-selected').length > 0) {
                                CWS.aria_live($('.pac-item-selected .pac-item-query').text() + ', ' + $('.pac-item-selected > span:last-child').text(), true);
                            }
                        }
                    });
                }
            });
        },
        page: page,
        next_page: function () {
            page++;
            var offset = $('.widget-jobsearch-results,.widget-jobsearch-results-list,.global-search-results-page').offset().top;
            var adminbarHeight = $('#wpadminbar').height();
            if(adminbarHeight)
                offset = offset - adminbarHeight;
            $('html, body').scrollTop(offset);
            focus_on_first_result = true;
            gather_criteria();
        },
        prev_page: function () {
            page--;
            var offset = $('.widget-jobsearch-results,.widget-jobsearch-results-list,.global-search-results-page').offset().top;
            var adminbarHeight = $('#wpadminbar').height();
            if(adminbarHeight)
                offset = offset - adminbarHeight;
            $('html, body').scrollTop(offset);
            focus_on_first_result = true;
            gather_criteria();
        },
        goto_page: function (num, focusOnFirstResult) {
            page = num;
            var offset = $('.widget-jobsearch-results,.widget-jobsearch-results-list,.global-search-results-page').offset().top;
            var adminbarHeight = $('#wpadminbar').height();
            if(adminbarHeight)
                offset = offset - adminbarHeight;
            $('html, body').scrollTop(offset);
            // focus_on_first_result = true;
            var tabAttr = $('#live-results').attr('tabindex');

            if(focusOnFirstResult === true){
                focus_on_first_result = true;
            }
            else if($('.search-results-title').is(':visible') ){
                $('.search-results-title').focus();
            }
            // attribute exists?
            else if (typeof tabAttr !== 'undefined' && tabAttr !== false) {

                $('#live-results').focus();
            } else {
                $('#live-results-counter').focus();
            }
            gather_criteria();

        },
        sortby: function (str, order) {
            default_sortfield = str;
            sortfield = str;
            if (order) {
                default_sortorder = order;
                sortorder = order;
            }
        },
        go_to_job: function (el) {
            var url = $(el).find('a').attr('href');
            window.location.href = url;
        },
        search: search_jobs,
        jobCallback: job_callback,
        jobCallbackRefreshFilter: job_refresh_filter,
        gather: gather_criteria,
        display: display_jobs,
        layout: function(){
            return options.view_by_layout;
        },
        mapPosition: function ()
        {
            return options.mapPosition;
        },
        loader: loader, // Job Map widget is going to use it
        init: function (parent, results) {
            window.blur_timer = null;
            //var gather_criteria = this.gather_critera;

            if (options.display_loading_bar && $('#loader').length !== 0) {
                // Uses MProgress loading bar. Template:3 = indeterminate load time.
                loader = new Mprogress({template: 3, parent: '#loader'});
            }

            if (options.pollinator_noresults === false) {
                $('#job-alert').hide();
            }

            if(typeof $('</div>').mdRipple === 'function') {
                $('.search-columns .flex_column').mdRipple();
            }

            if($('.widget-jobsearch-full-horizontal').length > 0){
                widgetDiv = 'widget-jobsearch-full-horizontal';
            }else if($('.widget-jobsearch-full').length > 0){
                widgetDiv = 'widget-jobsearch-full';
            }else{
                widgetDiv = 'widget-jobsearch-v2';
            }            

            var widget_jobsearch_full_horizontal = widgetDiv;

            parent = $('.'+widget_jobsearch_full_horizontal);

            results = $('#widget-jobsearch-results-list');
            pages = $('#widget-jobsearch-results-pages');
            old_keywords = $('#cws_jobsearch_keywords').val();
            loaded_page = parseInt($('#cws-search-page').val());

            if (loaded_page && loaded_page !== 0) {
                page = loaded_page - 1;
            }

            var unit_switch = $('.unit-switch');
            if (unit_switch.length > 0) {
                var switch_opts = unit_switch.data();
                switch_opts.height = 14;
                switch_opts = CWS.apply_filter('unit_switch_opts', switch_opts);
                if(unit_switch.switchButton){
                    unit_switch.switchButton(switch_opts);
                }

            }

            CWS.apply_filter('pre_column_event_listener', null);

            $('.search-columns .flex_column:not(.unsortable)').click(function (e) {
                var param = $(this).data('param');
                if(param == 'city_state_country_or_locationtype'){
                    param = 'primary_city';
                }
                if(cws_opts.api.includes('google') && param == 'open_date' && $(this).hasClass('active')) {
                    return; // For Google API - if the jobs are already sorted in open_date order, then we do nothing on click.
                }
                if (sortfield == param) {
                    sortorder = sortorder == 'ascending' ? 'descending' : 'ascending';
                }
                else {
                    sortfield = param;
                    sortorder = 'ascending';
                }

                sortorder = CWS.apply_filter('search_results_sort_order', sortorder, param);
                
                if(cws_opts.is_tablearrows_visibility_enabled === 'true'){
                    $('.search-columns').find('.col-controls').find('.col-control').removeClass('sorted');
                    if(param === 'primary_city'){
                        $('.search-columns').find('div[data-param='+param+']').find('.'+sortorder).addClass('sorted');
                    }else{
                        $("#colhead-"+param).find('.'+sortorder).addClass('sorted');
                    }
                }
                $('#cws-search-sortfield').val(sortfield);
                $('#cws-search-direction').val(sortorder);

                gather_criteria();
            });
            $('.search-columns .flex_column:not(.unsortable)').keypress(function(e){
                e.preventDefault();
                if(e.keyCode === 13 || e.keyCode === 32) {
                    $(this).click();
                }
            });

            var slider = $('#date-slider');
            if (slider.length > 0) {
                $('#date-slider .ui-slider-handle').focus(function () {
                    var val = slider.slider('value');
                    var msg = 'Posted date, ';

                    if (val === 1) {
                        msg += 'within the last twenty four hours, use right arrow to increase';
                    }
                    else if (val === 2) {
                        msg += 'within the last seven days, use left and right arrow keys to change';
                    }
                    else if (val === 3) {
                        msg += 'within the last thirty days, use left and right arrow keys to change';
                    }
                    else {
                        msg += 'any time, use left arrow to reduce';
                    }

                    $('#date-slider .ui-slider-handle').attr('role','slider').attr('aria-label',msg);
                });
            }

            var sort_options = $('.widget-jobsearch-results.table_tile #sort-by');
            if(sort_options.length > 0){
                sortfield = sort_options.val();
                sortorder = sort_options.find(':selected').data('sortdir');
                sort_options.on('change', function(){
                    sortfield = $(this).val();
                    sortorder = $(this).find(':selected').data('sortdir');
                    $('#cws-search-sortfield').val(sortfield);
                    $('#cws-search-direction').val(sortorder);
                    page = 0;
                    gather_criteria();
                });
            }

            var page_options = $('.widget-jobsearch-results.table_tile #result-pages');
            if(page_options.length > 0){
                options.limit = page_options.val();
                page_options.on('change', function(){
                    options.limit = $(this).val();
                    page = 0;
                    gather_criteria();
                });
            }

            // functionality of tiles and grid view
            var view_options_list = $('.widget-jobsearch-results.table_tile #result-view-list');
            if(view_options_list.length > 0) {
                view_options_list.on('click', function (event) {
                    event.preventDefault();
                    $('.widget-jobsearch-results').removeClass('tiles list map')
                        .addClass('list');
                    $('.widget-jobsearch-results').find('.search-results-table').attr("role","table");
                    $('#result-view').val('list');
                    CWS.cookies.setItem('view', 'list');
                });
            }

            var view_options_grid = $('.widget-jobsearch-results.table_tile #result-view-grid');
            if(view_options_grid.length > 0) {
                view_options_grid.on('click', function (event) {
                    event.preventDefault();
                    $('.widget-jobsearch-results').removeClass('tiles list map')
                        .addClass('tiles');
                    $('.widget-jobsearch-results').find('.search-results-table').attr("role","presentation");
                    $('#result-view').val('tiles');
                    CWS.cookies.setItem('view', 'tiles');
                });
            }

            var view_by = $('.widget-jobsearch-results.table_tile #result-view');
            if(view_by.length > 0) {
                view_by.on('change', function(event){
                    if(($(this).val() === 'map' && $('#job-map').css('display') === 'none')
                        || ($(this).val() !== 'map' && $('#job-map').css('display') === 'block')){
                        CWS.map.toggle($('#job-map-toggle'));
                    }
                    $('.widget-jobsearch-results').removeClass('tiles list map')
                        .addClass($(this).val());
                });
            }

            // to retain the value to check whether grid or tiles is selected

            var cookie_view = CWS.cookies.getItem('view');
            if(cookie_view !== null){
                $('#result-view').val(cookie_view);
            }

            var current_view = $('#result-view').val();
            if(current_view){
                $('.widget-jobsearch-results').removeClass('list')
                    .removeClass('tiles')
                    .addClass(current_view);
                if(current_view == 'tiles'){
                    $('#result-view-list').removeClass('active');
                    $('#result-view-grid').addClass('active');
                }else{
                    $('#result-view-grid').removeClass('active');
                    $('#result-view-list').addClass('active');
                }
            }

            // making the grid and tile view active on click
            $('.btn-group a').on('click', function(){
                $('.btn-group a.active').removeClass('active').attr('aria-current','false');
                $(this).addClass('active').attr('aria-current','true');
            });

            $('#search-filters').on('click', '.search-tag', function(e){
                var param = $(this).data('tag-param'),
                    facet = $(this).data('tag-facet'),
                    value = $(this).data('tag-value');
                if($('.search-tag').length === 1){
                    set_default_sort();
                }
                remove_criteria(param, facet, value, e);
                singleselect_accessibilty();
            });

            $('#search-filters').on('click', '.clear-tags', function(e){
                e.preventDefault();
                $('input[data-param],select[data-param]').each(function(){
                    if(!$(this).is('input[type="checkbox"]') && $(this).attr('id') !== 'cws_jobsearch__proximity') {
                        $(this).val('').trigger('change.select2');
                    }else if($(this).prop("checked") == true) {
                        $(this).prop('checked', false);
                        $(this).removeAttr('checked');
                    } else if ($(this).attr('id') === 'cws_jobsearch__proximity') {
                        var defaultOption = $(this).find('option[selected]');
                        var radius = defaultOption.val() ? defaultOption.val() : $(this).val();
                        $(this).val(radius);
                    }
                });
                set_default_sort();
                singleselect_accessibilty();
                gather_criteria(e);

                return false;
            });

            // Check to see if there's the search form, otherwise, otherwise this script can be used as utility (CWS.jobs.search())
            if (parent.length > 0) {
                if(cws_opts && cws_opts.personalization && CWS.storageAvailable('localStorage')){
                    var place_from_quick_search = localStorage.getItem('last_place');
                    if(place_from_quick_search){
                        last_place = JSON.parse(place_from_quick_search);
                        localStorage.removeItem('last_place');
                    }
                }

                parent.find('input:not(.loc_auto)').on('keypress', function(e){
                    if($('#job-live-search').length){
                        if($('#job-live-search').is(':checked')){
                            gather_criteria(e);
                        }
                    }
                    else{
                        gather_criteria(e);
                    }
                });
                parent.find('input.loc_auto').on('keyup', function (e) {
                    if (e.keyCode == 13 && $(this).val() == '') {
                        $('#cws_jobsearch_latitude,#cws_jobsearch_longitude').val('');
                        page = 0;

                        var $country = $('#cws_jobsearch_country'),
                            $state = $('#cws_jobsearch_state');
                        if ($country) {
                            $country.val('');
                            $state.val('');
                        }
                    }
                    if ($(this).val() === '') {
                        $('.location-wrapper .geolocation-icon').show().parent().addClass('with_geo');
                    }
                });
                parent.find('input.loc_auto').on('here_suggestion_selected', function(e, data){
                    $('#cws_jobsearch_latitude').val('');
                    $('#cws_jobsearch_longitude').val('');
                    $('#cws_jobsearch_country').val('');
                    $('#cws_jobsearch_state').val('');
                    $('#cws-search-sortfield').val('');
                    $('#cws-search-direction').val('');

                    $('#cws_jobsearch_latitude').val(data.location.position[0]);
                    $('#cws_jobsearch_longitude').val(data.location.position[1]);

                    gather_criteria();
                });
                parent.find('#cws-adv-search-btn').on('click', gather_criteria);

                if($('#job-live-search').length) {
                    parent.find('select').change(function(){
                        if($('#job-live-search').is(':checked')){
                            gather_criteria();
                        }
                    });
                    parent.find('input:checkbox:not(#job-live-search)').change(function (e) {
                        if($('#job-live-search').is(':checked')){
                            gather_criteria(e);
                            return true;
                        }
                    });
                }
                else {
                    parent.find('select').change(gather_criteria);
                    parent.find('input:checkbox:not(#job-live-search)').change(function (e) {
                        gather_criteria(e);
                        return true;
                    });
                }

                //Refresh dropdown in real time filter if Chaining Load Dropdown Filter(s) In Real Time is eanbled.
                parent.find('.search-control-container select').on('select2:open', function(e) {
                    if(options.dynamic_criteria && cws_opts.chaining_load_realtime_filter){
                        if(!$(this).hasClass("opendropdown")){
                            parent.find('select').removeClass("opendropdown");
                            $(this).addClass("opendropdown");

                            //Start Add Loader to current dropdown 
                            let select2Container =  $(this).next('.select2-container');
                            select2Container.addClass('loading');
                            if (!select2Container.find('.loading-spinner').length) {
                                select2Container.append('<div class="loading-spinner">'+CWS._('Loading...')+'</div>');
                                $('.select2-results').hide();
                            }
                            let refresh_open_filter  = true;
                            gather_criteria(e,false,refresh_open_filter);
                        }
                    }
                });

                var commute_radio = parent.find('#location_search_type_commute');
                if(commute_radio.length > 0){
                    var container = $('.location-box').removeClass('radius commute');
                    var current_val = $('#cws_jobsearch_location').val() || $('#cws_jobsearch_commute').val();
                    $('#cws_jobsearch_location,#cws_jobsearch_commute').val(current_val);

                    if(commute_radio.is(':checked')){
                        container.addClass('commute');
                    }
                    else{
                        container.addClass('radius');
                    }
                }
                parent.find('.location-search-type .location-search-type-options input').on('change', function (e){
                    $('.location-box').removeClass('radius commute').addClass($(this).val());
                    gather_criteria(e);
                });

                parent.find('#date-container.radios input').on('change', gather_criteria);
                if($('#date-container.radios').length > 0){
                    var right_value = $('#date-container.radios .date-radio-wrap').width() - $('#posted-date-4').position().left - 10;
                    $('#search-form-styles').html($('#search-form-styles').html() + ' .date-radio-wrap:before{right: ' + right_value + 'px;}');
                }

                if(options.dynamic_criteria) {
                    $('body').addClass('hide-disabled-options');
                }

                // No longer using ip detection for the location field, HTML5 button only.
                // Let's check if the user has already allowed it.
                var geolocation_addr = CWS.cookies.getItem('geolocation_addr'),
                    loc_field = $('#cws_jobsearch_location'),
                    geolocation_lat = CWS.cookies.getItem('geolocation_lat'),
                    lat_field = $('#cws_jobsearch_latitude'),
                    geolocation_lon = CWS.cookies.getItem('geolocation_lon'),
                    lon_field = $('#cws_jobsearch_longitude');

                // Check for querystring first, from refresh or quick search
                if (loc_field && loc_field.val()) {
                    sortfield = '';

                    if (loc_field.val() == geolocation_addr) {
                        $('.location-wrapper.with_geo .geolocation-icon').hide().parent().removeClass('with_geo');
                    }
                }

                    // Now see if the user has accepted html5 geolocation to prepopulate
                // Ignore them if one is somehow missing
                else if (geolocation_addr !== null && geolocation_lat !== null && geolocation_lon !== null) {
                    lat_field.val(geolocation_lat);
                    lon_field.val(geolocation_lon);
                    loc_field.val(geolocation_addr);

                    $('.location-wrapper.with_geo .geolocation-icon').hide().parent().removeClass('with_geo');
                }

                if (options.search_on_pause && cws_opts.api.indexOf('googleapi') === -1) {
                    parent.find('input[type=text]:not(.loc_auto)').on('keyup', function (e) {
                        // Alphanumeric only; may change later; also backspace
                        if ((e.which <= 90 && e.which >= 48) || e.which == 8) {
                            clearTimeout(window.blur_timer);
                            // window.blur_timer = null; //'blur_timer' was already set on line 1634. - SonarQube
                            window.blur_timer = setTimeout(function () {
                                // I don't like this solution to get around closure
                                parent.find('#cws-adv-search-btn').click();
                            }, 500);
                        }
                    });
                }

                if (old_keywords !== '' && old_keywords !== undefined) {
                    sortfield = '';
                }

                gather_criteria();
            }
            else {
                no_form = true;
            }

            // An odd check... the map plugin doesn't offer much in the way of events, let's see if there's a marker's array
            if (typeof allmarkers == 'object' && google) {
                $('.widget-jobsearch-results,.widget-jobsearch-results-list').hide();
                options.locations_page = true;
                google.setOnLoadCallback(setTimeout(function () {
                        for (var i = 0, len = allmarkers.length; i < len; i++) {
                            // We should definitely do performance tests on this
                            var marker = allmarkers[i],
                                criteria = {};
                            criteria.LocationRadius = 50;

                            google.maps.event.addListener(marker, "click", function (mrk) {
                                criteria.Latitude = mrk.latLng.lat();
                                criteria.Longitude = mrk.latLng.lng();

                                location_criteria = criteria;
                                page = 0;
                                $('.widget-jobsearch-results').slideDown();

                                search_jobs(criteria);
                            });
                        }
                    },
                    2000));
            }

            // Check for Leaflet maps
            if ($('.mapsmarker').length > 0) {
                $('.widget-jobsearch-results,.widget-jobsearch-results-list').hide();
                $('#job-alert').hide();
                options.locations_page = true;

                var layer_id = $('.mapsmarker.layermap').attr('class').replace(/.+?layer-(\d+).*/gi, '$1');

                if (window['layermap_' + layer_id]) {
                    window['layermap_' + layer_id].on('layeradd', function (e) {
                        if (window['markerID_layermap_' + layer_id] && !CWS.marker_events_set) {
                            CWS.marker_events_set = true;
                            for (mrk in window['markerID_layermap_' + layer_id]) {
                                if (!window['markerID_layermap_' + layer_id][mrk]['jobeventset']) {
                                    window['markerID_layermap_' + layer_id][mrk]['jobseventset'] = true;
                                    window['markerID_layermap_' + layer_id][mrk].on('click', function (em) {
                                        var criteria = {};
                                        if (options.locations_page_search_by === 'radius') {
                                            criteria.Latitude = em.latlng.lat;
                                            criteria.Longitude = em.latlng.lng;
                                            criteria.LocationRadius = options.locations_page_radius;
                                        }
                                        else if (options.locations_page_search_by === 'city') {
                                            var address = em.target.feature.properties.address;
                                            var address_regex = /(.+?),\s* (.+?),.*$/gi;
                                            var city = address.replace(address_regex, '$2');
                                            if (city) {
                                                criteria.facet = 'primary_city:' + city;
                                            }
                                            else {
                                                CWS.log('City not found. Address given: ' + address + '. City from regex: ' + city + '.');
                                                return;
                                            }
                                        }

                                        location_criteria = _.clone(criteria);
                                        page = 0;
                                        $('.widget-jobsearch-results,.widget-jobsearch-results-list').slideDown();

                                        search_jobs(criteria);
                                    });
                                }
                            }
                        }
                    });
                }
            }

        }
    }
})(window, jQuery);
